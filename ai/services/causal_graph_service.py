"""
Causal Graph Service for Monthly Newsletter System

This service manages causal relationships between metrics and detects
causal chains from metric changes and anomalies.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from tools.db_utils import execute_with_connection, get_pooled_connection

logger = logging.getLogger(__name__)


class CausalGraphService:
    """Service for managing and analyzing causal relationships between metrics."""
    
    def __init__(self, graph_file_path: Optional[str] = None):
        """
        Initialize the causal graph service.
        
        Args:
            graph_file_path: Path to causal graph JSON file. If None, uses default path.
        """
        if graph_file_path is None:
            # Default to ai/data/causal_graph.json relative to this file
            script_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(script_dir))
            graph_file_path = os.path.join(project_root, "ai", "data", "causal_graph.json")
        
        self.graph_file_path = graph_file_path
        self.graph = None
        self.metrics_cache = {}  # Cache metric lookups
        self._load_causal_graph()
    
    def _load_causal_graph(self) -> None:
        """Load causal graph from JSON file."""
        try:
            if not os.path.exists(self.graph_file_path):
                logger.error(f"Causal graph file not found: {self.graph_file_path}")
                self.graph = {
                    "version": "1.0",
                    "last_updated": "2025-01-15",
                    "relationships": [],
                    "chains": [],
                    "categories": {},
                    "validation_rules": {}
                }
                return
            
            with open(self.graph_file_path, 'r', encoding='utf-8') as f:
                self.graph = json.load(f)
            
            logger.info(f"Loaded causal graph from {self.graph_file_path}")
            logger.info(f"Graph contains {len(self.graph.get('relationships', []))} relationships")
            
            # Validate metric IDs exist in database
            self.validate_metric_ids()
            
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing causal graph JSON: {e}")
            self.graph = {
                "version": "1.0",
                "last_updated": "2025-01-15",
                "relationships": [],
                "chains": [],
                "categories": {},
                "validation_rules": {}
            }
        except Exception as e:
            logger.error(f"Error loading causal graph: {e}")
            self.graph = {
                "version": "1.0",
                "last_updated": "2025-01-15",
                "relationships": [],
                "chains": [],
                "categories": {},
                "validation_rules": {}
            }
    
    def get_metric_info(self, metric_id: int) -> Optional[Dict[str, Any]]:
        """
        Query metrics table to get current metric information.
        
        Args:
            metric_id: Metric ID to look up
            
        Returns:
            Dictionary with metric information or None if not found
        """
        # Check cache first
        if metric_id in self.metrics_cache:
            return self.metrics_cache[metric_id]
        
        def query_metric(connection):
            cursor = connection.cursor()
            cursor.execute("""
                SELECT id, metric_name, metric_key, category, subcategory, is_active
                FROM metrics
                WHERE id = %s
            """, (metric_id,))
            row = cursor.fetchone()
            cursor.close()
            
            if row:
                return {
                    "id": row[0],
                    "metric_name": row[1],
                    "metric_key": row[2],
                    "category": row[3],
                    "subcategory": row[4],
                    "is_active": row[5]
                }
            return None
        
        try:
            with get_pooled_connection() as conn:
                metric_info = query_metric(conn)
                if metric_info:
                    self.metrics_cache[metric_id] = metric_info
                return metric_info
        except Exception as e:
            logger.error(f"Error querying metric {metric_id}: {e}")
            return None
    
    def validate_metric_ids(self) -> Dict[str, Any]:
        """
        Validate all metric_ids in graph exist in metrics table.
        
        Returns:
            Dictionary with validation results
        """
        if not self.graph:
            return {"status": "error", "message": "Graph not loaded"}
        
        # Collect all metric IDs from relationships
        metric_ids = set()
        for rel in self.graph.get("relationships", []):
            if "source_metric_id" in rel:
                metric_ids.add(rel["source_metric_id"])
            if "target_metric_id" in rel:
                metric_ids.add(rel["target_metric_id"])
        
        # Also check chains
        for chain in self.graph.get("chains", []):
            if "metric_ids" in chain:
                metric_ids.update(chain["metric_ids"])
        
        if not metric_ids:
            return {"status": "success", "message": "No metric IDs to validate"}
        
        def validate_operation(connection):
            cursor = connection.cursor()
            cursor.execute("""
                SELECT id FROM metrics WHERE id = ANY(%s)
            """, (list(metric_ids),))
            existing_ids = {row[0] for row in cursor.fetchall()}
            cursor.close()
            return existing_ids
        
        try:
            with get_pooled_connection() as conn:
                existing_ids = validate_operation(conn)
            
            missing_ids = metric_ids - existing_ids
            if missing_ids:
                logger.warning(f"Found {len(missing_ids)} missing metric IDs in graph: {missing_ids}")
                return {
                    "status": "warning",
                    "message": f"Found {len(missing_ids)} missing metric IDs",
                    "missing_ids": list(missing_ids),
                    "total_checked": len(metric_ids),
                    "found": len(existing_ids)
                }
            else:
                logger.info(f"All {len(metric_ids)} metric IDs validated successfully")
                return {
                    "status": "success",
                    "message": f"All {len(metric_ids)} metric IDs validated",
                    "total_checked": len(metric_ids)
                }
        except Exception as e:
            logger.error(f"Error validating metric IDs: {e}")
            return {"status": "error", "message": str(e)}
    
    def detect_causal_chains(
        self, 
        metrics: List[Dict[str, Any]], 
        anomalies: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Detect causal chains from current metric changes/anomalies.
        
        Args:
            metrics: List of metrics with changes (from select_deltas_to_discuss)
            anomalies: Optional list of anomalies (from select_anomalies_to_discuss)
            
        Returns:
            Dictionary with detected chains, expected downstream effects, and unexpected relationships
        """
        if not self.graph:
            return {
                "detected_chains": [],
                "expected_downstream": [],
                "unexpected_relationships": []
            }
        
        # Create a map of metric_id to change information
        metric_changes = {}
        for metric in metrics:
            metric_id = metric.get("metric_id")
            if metric_id:
                try:
                    metric_id_int = int(metric_id)
                    metric_changes[metric_id_int] = {
                        "metric_id": metric_id_int,
                        "metric_name": metric.get("metric", "Unknown"),
                        "change_direction": self._determine_change_direction(metric),
                        "percent_change": metric.get("percent_change", 0),
                        "difference": metric.get("difference", 0),
                        "recent_mean": metric.get("recent_mean", 0),
                        "comparison_mean": metric.get("comparison_mean", 0)
                    }
                except (ValueError, TypeError):
                    continue
        
        # Also process anomalies
        if anomalies:
            for anomaly in anomalies:
                metric_id = anomaly.get("metric_id")
                if metric_id:
                    try:
                        metric_id_int = int(metric_id)
                        # Anomalies may override or supplement metric changes
                        if metric_id_int not in metric_changes:
                            metric_changes[metric_id_int] = {
                                "metric_id": metric_id_int,
                                "metric_name": anomaly.get("metric", "Unknown"),
                                "change_direction": self._determine_change_direction(anomaly),
                                "percent_change": anomaly.get("percent_change", 0),
                                "difference": anomaly.get("difference", 0),
                                "recent_mean": anomaly.get("recent_mean", 0),
                                "comparison_mean": anomaly.get("comparison_mean", 0),
                                "is_anomaly": True
                            }
                    except (ValueError, TypeError):
                        continue
        
        # Detect chains from graph
        detected_chains = []
        expected_downstream = []
        unexpected_relationships = []
        
        # Check each chain in the graph
        for chain in self.graph.get("chains", []):
            chain_metric_ids = chain.get("metric_ids", [])
            chain_metrics = []
            all_present = True
            
            for metric_id in chain_metric_ids:
                if metric_id in metric_changes:
                    chain_metrics.append(metric_changes[metric_id])
                else:
                    all_present = False
                    break
            
            if all_present and len(chain_metrics) >= 2:
                # Check if changes match expected relationships
                matches_expectation = self._validate_chain_expectations(
                    chain, chain_metrics
                )
                
                detected_chains.append({
                    "chain_id": chain.get("chain_id"),
                    "chain_name": chain.get("name"),
                    "description": chain.get("description"),
                    "metrics": chain_metrics,
                    "matches_expectation": matches_expectation,
                    "expected_lag_days": chain.get("expected_lag_days", 0)
                })
        
        # Check individual relationships for expected downstream effects
        for rel in self.graph.get("relationships", []):
            source_id = rel.get("source_metric_id")
            target_id = rel.get("target_metric_id")
            
            if source_id in metric_changes:
                source_change = metric_changes[source_id]
                expected_dir = rel.get("change_direction", "same")
                
                # Determine expected change direction for target
                if expected_dir == "same":
                    expected_target_dir = source_change["change_direction"]
                elif expected_dir == "opposite":
                    expected_target_dir = "up" if source_change["change_direction"] == "down" else "down"
                elif expected_dir == "inverse":
                    expected_target_dir = "up" if source_change["change_direction"] == "down" else "down"
                else:
                    expected_target_dir = "neutral"
                
                # Check if target also has a change
                target_observed = target_id in metric_changes
                observed_dir = None
                if target_observed:
                    observed_dir = metric_changes[target_id]["change_direction"]
                
                expected_downstream.append({
                    "source_metric_id": source_id,
                    "source_metric_name": source_change["metric_name"],
                    "target_metric_id": target_id,
                    "target_metric_name": self._get_metric_name(target_id),
                    "expected_change": expected_target_dir,
                    "confidence": rel.get("confidence", "medium"),
                    "observed": target_observed,
                    "observed_change": observed_dir,
                    "relationship_type": rel.get("relationship_type"),
                    "lag_days": rel.get("lag_days", 0)
                })
                
                # Check if observed matches expected
                if target_observed and observed_dir != expected_target_dir and expected_target_dir != "neutral":
                    # This is an unexpected relationship
                    unexpected_relationships.append({
                        "metric1_id": source_id,
                        "metric1_name": source_change["metric_name"],
                        "metric2_id": target_id,
                        "metric2_name": self._get_metric_name(target_id),
                        "relationship": rel.get("relationship_type", "unknown"),
                        "direction": rel.get("direction", "unknown"),
                        "expected": expected_target_dir,
                        "observed": observed_dir,
                        "reasoning": f"{source_change['metric_name']} changed {source_change['change_direction']} but {self._get_metric_name(target_id)} changed {observed_dir} (expected {expected_target_dir})"
                    })
        
        return {
            "detected_chains": detected_chains,
            "expected_downstream": expected_downstream,
            "unexpected_relationships": unexpected_relationships
        }
    
    def suggest_downstream_effects(
        self, 
        metric_id: int, 
        change_direction: str
    ) -> List[Dict[str, Any]]:
        """
        Suggest what downstream metrics should change based on causal graph.
        
        Args:
            metric_id: Source metric ID
            change_direction: Direction of change ("up", "down", "neutral")
            
        Returns:
            List of expected downstream changes with confidence levels
        """
        if not self.graph:
            return []
        
        suggestions = []
        
        for rel in self.graph.get("relationships", []):
            if rel.get("source_metric_id") == metric_id:
                target_id = rel.get("target_metric_id")
                expected_dir = rel.get("change_direction", "same")
                
                # Determine expected change direction
                if expected_dir == "same":
                    expected_target_dir = change_direction
                elif expected_dir in ["opposite", "inverse"]:
                    expected_target_dir = "up" if change_direction == "down" else "down"
                else:
                    expected_target_dir = "neutral"
                
                suggestions.append({
                    "target_metric_id": target_id,
                    "target_metric_name": self._get_metric_name(target_id),
                    "expected_change": expected_target_dir,
                    "confidence": rel.get("confidence", "medium"),
                    "relationship_type": rel.get("relationship_type"),
                    "lag_days": rel.get("lag_days", 0),
                    "description": rel.get("description", "")
                })
        
        return suggestions
    
    def validate_causal_relationships(
        self, 
        metrics: List[Dict[str, Any]], 
        anomalies: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Check if observed changes match expected causal relationships.
        
        Args:
            metrics: List of metrics with changes
            anomalies: Optional list of anomalies
            
        Returns:
            Validation results with matches/mismatches
        """
        analysis = self.detect_causal_chains(metrics, anomalies)
        
        # Count matches and mismatches
        matches = 0
        mismatches = 0
        
        for downstream in analysis["expected_downstream"]:
            if downstream["observed"]:
                if downstream["observed_change"] == downstream["expected_change"]:
                    matches += 1
                elif downstream["expected_change"] != "neutral":
                    mismatches += 1
        
        return {
            "status": "success",
            "matches": matches,
            "mismatches": mismatches,
            "total_checked": len(analysis["expected_downstream"]),
            "detected_chains": len(analysis["detected_chains"]),
            "unexpected_relationships": len(analysis["unexpected_relationships"]),
            "details": analysis
        }
    
    def _determine_change_direction(self, metric: Dict[str, Any]) -> str:
        """Determine change direction from metric data."""
        percent_change = metric.get("percent_change")
        difference = metric.get("difference", 0)
        
        if percent_change is not None:
            if percent_change > 0:
                return "up"
            elif percent_change < 0:
                return "down"
            else:
                return "neutral"
        elif difference > 0:
            return "up"
        elif difference < 0:
            return "down"
        else:
            return "neutral"
    
    def _validate_chain_expectations(
        self, 
        chain: Dict[str, Any], 
        chain_metrics: List[Dict[str, Any]]
    ) -> bool:
        """Validate if chain metrics match expected relationships."""
        if len(chain_metrics) < 2:
            return True  # Single metric chains always match
        
        # Check relationships between consecutive metrics in chain
        metric_ids = chain.get("metric_ids", [])
        relationships = self.graph.get("relationships", [])
        
        for i in range(len(metric_ids) - 1):
            source_id = metric_ids[i]
            target_id = metric_ids[i + 1]
            
            # Find relationship
            rel = None
            for r in relationships:
                if (r.get("source_metric_id") == source_id and 
                    r.get("target_metric_id") == target_id):
                    rel = r
                    break
            
            if rel:
                source_change = next(
                    (m for m in chain_metrics if m["metric_id"] == source_id), 
                    None
                )
                target_change = next(
                    (m for m in chain_metrics if m["metric_id"] == target_id), 
                    None
                )
                
                if source_change and target_change:
                    expected_dir = rel.get("change_direction", "same")
                    if expected_dir == "same":
                        if source_change["change_direction"] != target_change["change_direction"]:
                            return False
                    elif expected_dir in ["opposite", "inverse"]:
                        expected_target = "up" if source_change["change_direction"] == "down" else "down"
                        if target_change["change_direction"] != expected_target:
                            return False
        
        return True
    
    def _get_metric_name(self, metric_id: int) -> str:
        """Get metric name from cache or database."""
        if metric_id in self.metrics_cache:
            return self.metrics_cache[metric_id].get("metric_name", f"Metric {metric_id}")
        
        metric_info = self.get_metric_info(metric_id)
        if metric_info:
            return metric_info.get("metric_name", f"Metric {metric_id}")
        
        return f"Metric {metric_id}"











