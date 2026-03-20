import structlog
from typing import List, Optional
from dataclasses import dataclass, asdict

logger = structlog.get_logger()


@dataclass
class DocumentChunk:
    """A chunk of a document for processing"""
    document_id: str
    chunk_index: int
    content: str
    section: Optional[str]
    start_char: int
    end_char: int
    word_count: int


class SemanticChunker:
    """Chunk documents with section awareness"""
    
    def __init__(
        self,
        chunk_size: int = 750,      # Target words per chunk
        chunk_overlap: int = 50,     # Overlap in words
        min_chunk_size: int = 100    # Minimum chunk size
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size
        logger.info(f"📦 Chunker initialized: size={chunk_size}, overlap={chunk_overlap}, min={min_chunk_size}")
    
    def chunk_document(
        self,
        document_id: str,
        content: str,
        sections: dict
    ) -> List[DocumentChunk]:
        """Split document into overlapping chunks"""
        chunks = []
        
        # Chunk each section separately to preserve context
        if sections:
            logger.info(f"  📑 Chunking {len(sections)} sections...")
            for section_name, section_content in sections.items():
                if section_content and len(section_content.strip()) > 0:
                    section_chunks = self._chunk_text(
                        section_content,
                        document_id,
                        section_name
                    )
                    chunks.extend(section_chunks)
                    logger.info(f"    • {section_name}: {len(section_chunks)} chunks")
        
        # If no sections or sections didn't cover much, chunk the full content
        if not chunks:
            logger.info(f"  📄 Chunking full document content...")
            chunks = self._chunk_text(content, document_id, None)
        
        # Re-index chunks sequentially
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i
        
        logger.info(f"  ✅ Created {len(chunks)} chunks total")
        return chunks
    
    def _chunk_text(
        self,
        text: str,
        doc_id: str,
        section: Optional[str]
    ) -> List[DocumentChunk]:
        """Split text into overlapping chunks"""
        if not text or not text.strip():
            return []
        
        words = text.split()
        chunks = []
        
        if len(words) <= self.min_chunk_size:
            # Text too small to chunk, return as single chunk
            return [DocumentChunk(
                document_id=doc_id,
                chunk_index=0,
                content=text,
                section=section,
                start_char=0,
                end_char=len(text),
                word_count=len(words)
            )]
        
        start_idx = 0
        chunk_index = 0
        
        while start_idx < len(words):
            end_idx = min(start_idx + self.chunk_size, len(words))
            
            # Don't create tiny final chunks
            if len(words) - end_idx < self.min_chunk_size:
                end_idx = len(words)
            
            chunk_words = words[start_idx:end_idx]
            chunk_content = " ".join(chunk_words)
            
            # Calculate character positions (approximate)
            start_char = len(" ".join(words[:start_idx])) if start_idx > 0 else 0
            end_char = start_char + len(chunk_content)
            
            chunks.append(DocumentChunk(
                document_id=doc_id,
                chunk_index=chunk_index,
                content=chunk_content,
                section=section,
                start_char=start_char,
                end_char=end_char,
                word_count=len(chunk_words)
            ))
            
            # Move forward with overlap
            start_idx = end_idx - self.chunk_overlap
            chunk_index += 1
            
            if end_idx >= len(words):
                break
        
        return chunks


# Factory function to create chunker with custom settings
def create_chunker(chunk_size: int = 750, chunk_overlap: int = 50, min_chunk_size: int = 100) -> SemanticChunker:
    return SemanticChunker(chunk_size, chunk_overlap, min_chunk_size)