from tools.embedding import get_embedding
import qdrant_client
import re
from typing import List, Dict
import tiktoken

qdrant = qdrant_client.QdrantClient(host="localhost", port=6333)

def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in a text string using tiktoken."""
    encoder = tiktoken.get_encoding("cl100k_base")
    return len(encoder.encode(text))

def trim_results_to_token_limit(results: List[Dict], max_tokens: int = 100000) -> List[Dict]:
    """Trim results list if total tokens exceed max_tokens."""
    total_tokens = 0
    trimmed_results = []
    
    for result in results:
        content = result.get('content', '')
        tokens = estimate_tokens(content)
        
        if total_tokens + tokens <= max_tokens:
            trimmed_results.append(result)
            total_tokens += tokens
        else:
            break
            
    return trimmed_results

def query_qdrant(query, collection_name, top_k=4, score_threshold=0.45):
    try:
        embedded_query = get_embedding(query)
        if not embedded_query:
            print("Failed to generate embedding for query.")
            return []
        else:
            print(f"Embedding generated for query: type={type(embedded_query)}, length={len(embedded_query)}")
    except Exception as e:
        print(f"Error during embedding generation: {e}")
        return []

    try:
        query_results = qdrant.search(
            collection_name=collection_name,
            query_vector=embedded_query,
            limit=top_k,
            with_payload=True,
            score_threshold=score_threshold
        )

        # If there's a last_updated_date field, sort by it descending
        sorted_results = sorted(
            query_results,
            key=lambda x: x.payload.get('last_updated_date', 0),
            reverse=True
        )
        return sorted_results
    except Exception as e:
        print(f"Error querying Qdrant: {e}")
        return []

def query_docs(context_variables, collection_name, query):
    if not collection_name:
        collection_name = "2024-11"
    print(f"Searching collection '{collection_name}' with query: {query}")
    query_results = query_qdrant(query, collection_name=collection_name)
    
    
    try:
        serialized_results = [article.payload for article in query_results]
        
        # print("first few characgters of results:")
        # print(str(serialized_results[0])[:100])
    except Exception as e:
        print(f"Error serializing query results: {e}")

    if not query_results:
        print("No results")
        return {"response": "No results found.", "results": []}

    # Branch processing based on collection name
    if collection_name == "SFPublicData":
        # Process as docs
        return process_as_docs(query_results)
    else:
        # Process as content or other type
        return process_as_content(query_results)

def process_as_docs(query_results):
    output = []
    response = "Top results:\n\n"

    for i, article in enumerate(query_results, 1):
        payload = article.payload
        title = payload.get("title", "No Title")
        description = payload.get("description", "")
        endpoint = payload.get("endpoint", "No Endpoint")
        columns = payload.get("columns", {})
        queries = payload.get("queries", {})
        column_names = list(columns.keys())
        columns_formatted = ", ".join(column_names) if column_names else "No Columns Available"
        truncated_description = re.sub(
            r"\s+", " ", (description[:100] + "...") if len(description) > 100 else description
        )

        response += (
            f"{i}. **Title:** {title}\n"
            f"   **Description:** {truncated_description}\n"
            f"   **Endpoint:** {endpoint}\n"
            f"   **Columns:** {columns_formatted}\n\n"
            f"   **Queries:** {queries}\n\n"
        )

        output.append((title, description, endpoint, columns_formatted, column_names))

    return {"response": response, "results": output}

def process_as_content(query_results):
    output = []
    response = "Top matches:\n\n"

    for i, result in enumerate(query_results, 1):
        payload = result.payload
        
        # Handle YTD collection data
        if 'metric_name' in payload:
            # Format the metric information
            metric_info = (
                f"{i}. **Metric:** {payload.get('metric_name')}\n"
                f"   **Category:** {payload.get('category')}\n"
                f"   **District:** {payload.get('district_name')}\n"
                f"   **Current Year:** {payload.get('this_year')}\n"
                f"   **Last Year:** {payload.get('last_year')}\n"
                f"   **Last Updated:** {payload.get('last_data_date')}\n"
                f"   **Summary:** {payload.get('summary')}\n\n"
            )
            response += metric_info
            output.append({
                'metric_name': payload.get('metric_name'),
                'category': payload.get('category'),
                'district': payload.get('district_name'),
                'this_year': payload.get('this_year'),
                'last_year': payload.get('last_year'),
                'last_data_date': payload.get('last_data_date'),
                'summary': payload.get('summary'),
                'definition': payload.get('definition'),
                'score': result.score
            })
        else:
            # Handle other collections (fallback to original content processing)
            content = payload.get('content', 'No Content')
            truncated_content = re.sub(
                r"\s+", " ", (content[:100] + "...") if len(content) > 100 else content
            )
            response += f"{i}. **Content:** {truncated_content}\n\n"
            output.append({"content": content})

    # Add token limit check
    output = trim_results_to_token_limit(output)
    
    # Rebuild response with trimmed results if needed
    if len(output) < len(query_results):
        response = f"Top {len(output)} matches (results trimmed to stay within token limit):\n\n"
        for i, result in enumerate(output, 1):
            if 'metric_name' in result:
                response += (
                    f"{i}. **Metric:** {result['metric_name']}\n"
                    f"   **Category:** {result['category']}\n"
                    f"   **District:** {result['district']}\n"
                    f"   **Current Year:** {result['this_year']}\n"
                    f"   **Last Year:** {result['last_year']}\n"
                    f"   **Last Updated:** {result['last_data_date']}\n"
                    f"   **Summary:** {result['summary']}\n\n"
                )
            else:
                content = result["content"]
                truncated_content = re.sub(
                    r"\s+", " ", (content[:100] + "...") if len(content) > 100 else content
                )
                response += f"{i}. **Content:** {truncated_content}\n\n"

    return {"response": response, "results": output}
