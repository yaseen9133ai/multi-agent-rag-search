from abc import ABC, abstractmethod
from pathlib import Path
from typing import Union, List, Optional

class BaseEmbedding(ABC):
    """
    Base class for Multimodal Embedding providers.
    """

    @abstractmethod
    def embed_image(self, image_path: Union[str, Path]) -> List[float]:
        """
        Generates an embedding for a single image.
        """
        pass

    @abstractmethod
    def embed_batch(self, image_paths: List[Union[str, Path]]) -> List[List[float]]:
        """
        Generates embeddings for a batch of images.
        """
        pass

    @abstractmethod
    def embed_text(self, text: str) -> List[float]:
        """
        Generates an embedding for a text string.
        """
        pass