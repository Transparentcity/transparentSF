"""
Research Service - Modular service for managing research agendas and context.

This service ensures research agendas generated at high levels (e.g., monthly report
prioritization) are carried through to lower-level agents in the hierarchical agent graph,
maintaining research context and vision throughout the analysis pipeline.

Self-contained research service for TransparentSF.
"""

import logging
import json
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field, asdict
from dataclasses_json import dataclass_json

logger = logging.getLogger(__name__)


class ResearchType(str, Enum):
    """Types of research that can be conducted."""
    CAUSAL = "causal"  # Investigating causal relationships
    TREND = "trend"  # Analyzing trends over time
    ANOMALY = "anomaly"  # Investigating anomalies
    CORRELATION = "correlation"  # Finding correlations
    PREDICTIVE = "predictive"  # Making predictions
    EXPLANATORY = "explanatory"  # Explaining observed patterns
    COMPARATIVE = "comparative"  # Comparing across districts/time periods


@dataclass_json
@dataclass
class SuggestedMetric:
    """A metric suggested for investigation in a research agenda."""
    metric_name: str
    reason: str
    metric_id: Optional[str] = None
    priority: int = 1  # 1=highest, 5=lowest


@dataclass_json
@dataclass
class CausalInference:
    """A causal relationship identified in the research."""
    relationship: str  # e.g., 'property_crime_down ↔ drone_flights_up'
    direction: Optional[str] = None  # 'positive', 'negative', 'bidirectional'
    confidence: Optional[float] = None  # 0.0 to 1.0
    evidence: Optional[str] = None


@dataclass_json
@dataclass
class ResearchAgenda:
    """
    A research agenda containing the narrative thread, questions, and context
    for a research investigation.
    
    This model ensures research context is preserved and passed to lower-level agents.
    """
    narrative_thread: str
    research_questions: List[str]
    id: Optional[str] = None
    suggested_metrics: List[SuggestedMetric] = field(default_factory=list)
    causal_inferences: List[CausalInference] = field(default_factory=list)
    research_type: str = "causal"  # ResearchType value
    city_id: Optional[int] = None
    district: Optional[str] = None
    period_type: Optional[str] = None  # 'month', 'quarter', 'year'
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    status: str = "active"  # 'active', 'completed', 'archived', 'paused'
    priority: int = 1  # 1=highest, 5=lowest
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Initialize default values."""
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat()
        if not self.updated_at:
            self.updated_at = datetime.utcnow().isoformat()
    
    def to_context_string(self) -> str:
        """
        Convert research agenda to a context string that can be injected
        into agent prompts at lower levels.
        """
        context_parts = [
            f"## Research Agenda: {self.narrative_thread}",
            "",
            "### Research Questions:",
        ]
        
        for i, question in enumerate(self.research_questions, 1):
            context_parts.append(f"{i}. {question}")
        
        if self.suggested_metrics:
            context_parts.extend([
                "",
                "### Suggested Metrics to Investigate:",
            ])
            for metric in self.suggested_metrics:
                if isinstance(metric, dict):
                    context_parts.append(f"- {metric.get('metric_name', 'Unknown')} (Priority {metric.get('priority', 1)}): {metric.get('reason', '')}")
                else:
                    context_parts.append(f"- {metric.metric_name} (Priority {metric.priority}): {metric.reason}")
        
        if self.causal_inferences:
            context_parts.extend([
                "",
                "### Causal Relationships to Explore:",
            ])
            for inference in self.causal_inferences:
                if isinstance(inference, dict):
                    context_parts.append(f"- {inference.get('relationship', 'Unknown')}")
                    if inference.get('evidence'):
                        context_parts.append(f"  Evidence: {inference['evidence']}")
                else:
                    context_parts.append(f"- {inference.relationship}")
                    if inference.evidence:
                        context_parts.append(f"  Evidence: {inference.evidence}")
        
        if self.metadata:
            context_parts.extend([
                "",
                "### Additional Context:",
                json.dumps(self.metadata, indent=2)
            ])
        
        return "\n".join(context_parts)
    
    def to_compact_summary(self) -> str:
        """Create a compact summary for use in prompts."""
        questions_str = "; ".join(self.research_questions[:3])  # First 3 questions
        if len(self.research_questions) > 3:
            questions_str += f" (+{len(self.research_questions) - 3} more)"
        
        return f"Research: {self.narrative_thread}. Questions: {questions_str}"


class ResearchService:
    """
    Service for managing research agendas and ensuring research context
    flows through the hierarchical agent graph.
    
    This service provides:
    - Storage and retrieval of research agendas
    - Context injection for agents
    - Extensibility hooks for different research types
    - Integration with the agent system
    """
    
    def __init__(self, storage_backend: Optional[Any] = None):
        """
        Initialize the research service.
        
        Args:
            storage_backend: Optional storage backend (database, file, etc.)
                           If None, uses in-memory storage
        """
        self.storage_backend = storage_backend
        self._agendas: Dict[str, ResearchAgenda] = {}
        self.logger = logging.getLogger(__name__)
    
    def create_agenda(
        self,
        narrative_thread: str,
        research_questions: List[str],
        suggested_metrics: Optional[List[Dict[str, Any]]] = None,
        causal_inferences: Optional[List[Dict[str, Any]]] = None,
        research_type: ResearchType = ResearchType.CAUSAL,
        city_id: Optional[int] = None,
        district: Optional[str] = None,
        period_type: Optional[str] = None,
        priority: int = 1,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ResearchAgenda:
        """
        Create a new research agenda.
        
        Args:
            narrative_thread: Coherent narrative describing the research focus
            research_questions: List of well-formed research questions
            suggested_metrics: Optional list of metric dicts with 'metric_name', 'reason', 'priority'
            causal_inferences: Optional list of causal inference dicts
            research_type: Type of research being conducted
            city_id: City this research applies to
            district: District this research applies to
            period_type: Time period type
            priority: Overall priority (1=highest, 5=lowest)
            metadata: Additional metadata
            
        Returns:
            Created ResearchAgenda
        """
        # Convert suggested_metrics dicts to SuggestedMetric objects
        metrics = []
        if suggested_metrics:
            for m in suggested_metrics:
                if isinstance(m, dict):
                    metrics.append(SuggestedMetric(
                        metric_name=m.get('metric_name', ''),
                        reason=m.get('reason', ''),
                        metric_id=m.get('metric_id'),
                        priority=m.get('priority', 1)
                    ))
                elif isinstance(m, SuggestedMetric):
                    metrics.append(m)
        
        # Convert causal_inferences dicts to CausalInference objects
        inferences = []
        if causal_inferences:
            for inf in causal_inferences:
                if isinstance(inf, dict):
                    inferences.append(CausalInference(
                        relationship=inf.get('relationship', ''),
                        direction=inf.get('direction'),
                        confidence=inf.get('confidence'),
                        evidence=inf.get('evidence')
                    ))
                elif isinstance(inf, CausalInference):
                    inferences.append(inf)
                elif isinstance(inf, str):
                    # Simple string format: "metric1 ↔ metric2"
                    inferences.append(CausalInference(relationship=inf))
        
        # Get research type value
        research_type_value = research_type.value if isinstance(research_type, ResearchType) else research_type
        
        agenda = ResearchAgenda(
            narrative_thread=narrative_thread,
            research_questions=research_questions,
            suggested_metrics=metrics,
            causal_inferences=inferences,
            research_type=research_type_value,
            city_id=city_id,
            district=district,
            period_type=period_type,
            priority=priority,
            metadata=metadata or {}
        )
        
        # Store the agenda
        self._agendas[agenda.id] = agenda
        
        # Persist to backend if available
        if self.storage_backend:
            self._persist_agenda(agenda)
        
        self.logger.info(f"Created research agenda: {agenda.id} - {agenda.narrative_thread[:50]}...")
        
        return agenda
    
    def get_agenda(self, agenda_id: str) -> Optional[ResearchAgenda]:
        """Retrieve a research agenda by ID."""
        if agenda_id in self._agendas:
            return self._agendas[agenda_id]
        
        # Try to load from backend
        if self.storage_backend:
            return self._load_agenda(agenda_id)
        
        return None
    
    def get_active_agendas(
        self,
        city_id: Optional[int] = None,
        district: Optional[str] = None,
        research_type: Optional[ResearchType] = None
    ) -> List[ResearchAgenda]:
        """
        Get all active research agendas, optionally filtered.
        
        Args:
            city_id: Filter by city
            district: Filter by district
            research_type: Filter by research type
            
        Returns:
            List of active research agendas
        """
        agendas = [
            agenda for agenda in self._agendas.values()
            if agenda.status == "active"
        ]
        
        if city_id is not None:
            agendas = [a for a in agendas if a.city_id == city_id]
        
        if district is not None:
            agendas = [a for a in agendas if a.district == district]
        
        if research_type is not None:
            research_type_value = research_type.value if isinstance(research_type, ResearchType) else research_type
            agendas = [a for a in agendas if a.research_type == research_type_value]
        
        # Sort by priority (1=highest)
        agendas.sort(key=lambda a: a.priority)
        
        return agendas
    
    def get_research_context(
        self,
        city_id: Optional[int] = None,
        district: Optional[str] = None,
        research_type: Optional[ResearchType] = None,
        max_agendas: int = 3
    ) -> str:
        """
        Get research context string for injection into agent prompts.
        
        This is the key method for ensuring research agendas are carried
        to lower-level agents in the hierarchical graph.
        
        Args:
            city_id: Filter by city
            district: Filter by district
            research_type: Filter by research type
            max_agendas: Maximum number of agendas to include
            
        Returns:
            Formatted context string for agent prompts
        """
        agendas = self.get_active_agendas(
            city_id=city_id,
            district=district,
            research_type=research_type
        )[:max_agendas]
        
        if not agendas:
            return ""
        
        context_parts = [
            "## Active Research Agendas",
            "",
            "The following research agendas provide context for your analysis. ",
            "Ensure your work aligns with these research questions and objectives:",
            ""
        ]
        
        for i, agenda in enumerate(agendas, 1):
            context_parts.append(f"### Research Agenda {i}")
            context_parts.append(agenda.to_context_string())
            context_parts.append("")
        
        return "\n".join(context_parts)
    
    def update_agenda(
        self,
        agenda_id: str,
        **updates
    ) -> Optional[ResearchAgenda]:
        """Update an existing research agenda."""
        agenda = self.get_agenda(agenda_id)
        if not agenda:
            return None
        
        # Update fields
        for key, value in updates.items():
            if hasattr(agenda, key):
                setattr(agenda, key, value)
        
        agenda.updated_at = datetime.utcnow().isoformat()
        
        # Persist if backend available
        if self.storage_backend:
            self._persist_agenda(agenda)
        
        self.logger.info(f"Updated research agenda: {agenda_id}")
        
        return agenda
    
    def archive_agenda(self, agenda_id: str) -> bool:
        """Archive a research agenda (mark as completed)."""
        agenda = self.get_agenda(agenda_id)
        if not agenda:
            return False
        
        agenda.status = "archived"
        agenda.updated_at = datetime.utcnow().isoformat()
        
        if self.storage_backend:
            self._persist_agenda(agenda)
        
        return True
    
    def clear_all_agendas(self) -> int:
        """Clear all research agendas. Returns count of cleared agendas."""
        count = len(self._agendas)
        self._agendas.clear()
        self.logger.info(f"Cleared {count} research agendas")
        return count
    
    def _persist_agenda(self, agenda: ResearchAgenda) -> None:
        """Persist agenda to storage backend (to be implemented based on backend type)."""
        # This is a hook for different storage backends
        # Could be database, file, Redis, etc.
        pass
    
    def _load_agenda(self, agenda_id: str) -> Optional[ResearchAgenda]:
        """Load agenda from storage backend (to be implemented based on backend type)."""
        # This is a hook for different storage backends
        return None
    
    def from_monthly_report_data(
        self,
        research_agendas_data: List[Dict[str, Any]],
        city_id: Optional[int] = None,
        district: Optional[str] = None,
        period_type: str = "month"
    ) -> List[ResearchAgenda]:
        """
        Create research agendas from monthly report prioritization output.
        
        This method bridges the gap between the monthly report system
        and the research service.
        
        Args:
            research_agendas_data: List of research agenda dicts from prioritize_deltas
            city_id: City ID
            district: District
            period_type: Period type
            
        Returns:
            List of created ResearchAgenda objects
        """
        created_agendas = []
        
        for agenda_data in research_agendas_data:
            try:
                agenda = self.create_agenda(
                    narrative_thread=agenda_data.get("narrative_thread", ""),
                    research_questions=agenda_data.get("research_questions", []),
                    suggested_metrics=agenda_data.get("suggested_metrics", []),
                    causal_inferences=agenda_data.get("causal_inferences", []),
                    city_id=city_id,
                    district=district,
                    period_type=period_type
                )
                created_agendas.append(agenda)
            except Exception as e:
                self.logger.error(f"Error creating agenda from monthly report data: {e}")
                continue
        
        return created_agendas


# Global instance
_research_service: Optional[ResearchService] = None


def get_research_service() -> ResearchService:
    """Get the global research service instance."""
    global _research_service
    if _research_service is None:
        _research_service = ResearchService()
    return _research_service


def set_research_service(service: ResearchService) -> None:
    """Set the global research service instance (for dependency injection)."""
    global _research_service
    _research_service = service

