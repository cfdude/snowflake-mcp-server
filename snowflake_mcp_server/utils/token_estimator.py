"""Token estimation utilities for query result size prediction."""

import hashlib
import logging
import statistics
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TokenEstimator:
    """Estimates token count for query results based on configuration."""
    
    def __init__(self, config):
        """Initialize with OutputConfig."""
        self.config = config
        self.safe_token_limit = int(config.model_token_limit * config.safety_margin)
        
    async def estimate_query_tokens(
        self, 
        query: str, 
        db_ops,
        sample_size: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Estimate tokens for a query result based on sampling.
        
        Returns:
        {
            "row_count": 50000,
            "column_count": 17,
            "sample_tokens_per_row": 85,
            "estimated_total_tokens": 4_250_000,
            "estimated_size_kb": 17_000,
            "confidence": "high"
        }
        """
        sample_size = sample_size or self.config.token_sample_size
        
        try:
            # Get total row count with timeout protection
            count_query = f"SELECT COUNT(*) FROM ({query}) AS count_subq"
            count_result = await db_ops.execute_query_one(count_query)
            total_rows = count_result[0] if count_result else 0
            
            if total_rows == 0:
                return {
                    "row_count": 0,
                    "column_count": 0,
                    "sample_tokens_per_row": 0,
                    "estimated_total_tokens": 0,
                    "estimated_size_kb": 0,
                    "confidence": "high"
                }
            
            # Sample rows for token estimation (limit to avoid excessive sampling)
            actual_sample_size = min(sample_size, total_rows, 1000)
            sample_query = f"{query} LIMIT {actual_sample_size}"
            sample_rows, columns = await db_ops.execute_query(sample_query)
            
            if not sample_rows:
                return {
                    "row_count": total_rows,
                    "column_count": len(columns) if columns else 0,
                    "sample_tokens_per_row": 0,
                    "estimated_total_tokens": 0,
                    "estimated_size_kb": 0,
                    "confidence": "low"
                }
            
            # Calculate tokens per row
            token_counts = []
            for row in sample_rows:
                row_text = self._row_to_text(row, columns)
                tokens = self._estimate_tokens_from_text(row_text)
                token_counts.append(tokens)
            
            # Statistical analysis of token distribution
            avg_tokens = sum(token_counts) / len(token_counts)
            std_dev = statistics.stdev(token_counts) if len(token_counts) > 1 else 0
            
            # Confidence based on variance in token counts
            coefficient_of_variation = std_dev / avg_tokens if avg_tokens > 0 else 0
            if coefficient_of_variation < 0.2:
                confidence = "high"
            elif coefficient_of_variation < 0.5:
                confidence = "medium"
            else:
                confidence = "low"
            
            estimated_total_tokens = int(total_rows * avg_tokens)
            estimated_size_kb = int((estimated_total_tokens * 4) / 1024)  # ~4 chars per token
            
            result = {
                "row_count": total_rows,
                "column_count": len(columns),
                "sample_size": len(sample_rows),
                "sample_tokens_per_row": round(avg_tokens, 1),
                "estimated_total_tokens": estimated_total_tokens,
                "estimated_size_kb": estimated_size_kb,
                "confidence": confidence,
                "token_variance": round(std_dev, 1)
            }
            
            if self.config.log_token_estimation:
                logger.info(f"Token estimation: {result}")
            
            return result
            
        except Exception as e:
            logger.error(f"Error during token estimation: {e}")
            # Return conservative estimate on error
            return {
                "row_count": 0,
                "column_count": 0,
                "sample_tokens_per_row": 0,
                "estimated_total_tokens": 999999,  # Force file output on error
                "estimated_size_kb": 999999,
                "confidence": "error",
                "error": str(e)
            }
    
    async def should_use_file_output(
        self,
        query: str,
        db_ops,
        force_output: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Determines if file output should be used.
        Returns (use_file, reasoning_dict)
        """
        # Respect forced output mode
        if force_output == "file":
            return True, {"reason": "User requested file output", "forced": True}
        elif force_output == "screen":
            return False, {"reason": "User requested screen output", "forced": True}
        
        # Auto mode - estimate tokens
        estimate = await self.estimate_query_tokens(query, db_ops)
        estimated_tokens = estimate["estimated_total_tokens"]
        
        if self.config.log_token_estimation:
            logger.info(f"Token estimation for {self.config.model_name}: "
                       f"{estimated_tokens:,} tokens "
                       f"(limit: {self.config.model_token_limit:,}, "
                       f"safe: {self.safe_token_limit:,})")
        
        if estimated_tokens > self.safe_token_limit:
            return True, {
                "reason": f"Estimated {estimated_tokens:,} tokens exceeds "
                         f"{self.config.model_name} safe limit of {self.safe_token_limit:,} tokens",
                "estimated_tokens": estimated_tokens,
                "model_limit": self.config.model_token_limit,
                "safe_limit": self.safe_token_limit,
                "estimate_details": estimate,
                "forced": False
            }
        
        return False, {
            "reason": f"Estimated {estimated_tokens:,} tokens fits within "
                     f"{self.config.model_name} safe limit of {self.safe_token_limit:,} tokens",
            "estimated_tokens": estimated_tokens,
            "model_limit": self.config.model_token_limit,
            "safe_limit": self.safe_token_limit,
            "estimate_details": estimate,
            "forced": False
        }
    
    def _row_to_text(self, row: Tuple[Any, ...], columns: List[str]) -> str:
        """Convert a database row to text representation."""
        text_parts = []
        
        # Include column headers for more accurate token estimation
        for i, (col_name, value) in enumerate(zip(columns, row)):
            if value is None:
                text_parts.append(f"{col_name}: NULL")
            else:
                # Convert value to string, handling different data types
                str_value = str(value)
                # Truncate very long values for token estimation
                if len(str_value) > 1000:
                    str_value = str_value[:997] + "..."
                text_parts.append(f"{col_name}: {str_value}")
        
        return " | ".join(text_parts)
    
    def _estimate_tokens_from_text(self, text: str) -> float:
        """
        Estimate tokens from text using simple heuristic.
        
        This is a rough approximation:
        - 1 token ≈ 4 characters on average
        - Adjust for common patterns in structured data
        """
        if not text:
            return 0
        
        # Basic character-based estimation
        char_count = len(text)
        base_tokens = char_count / 4.0
        
        # Adjust for structured data patterns
        # SQL results tend to be more repetitive and structured
        pipe_count = text.count('|')  # Column separators
        colon_count = text.count(':')  # Key-value separators
        
        # Structured data tends to compress better in tokenization
        structure_factor = 0.85 if (pipe_count > 2 or colon_count > 2) else 1.0
        
        return base_tokens * structure_factor
    
    def get_query_hash(self, query: str) -> str:
        """Generate a hash for the query for filename generation."""
        return hashlib.md5(query.encode()).hexdigest()[:8]