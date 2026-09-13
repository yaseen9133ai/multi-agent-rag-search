import base64
import os
import asyncio
from pathlib import Path
from typing import Union, List
import cohere
from .base import BaseEmbedding

class CohereMultimodalEmbedding(BaseEmbedding):
    """
    Cohere implementation for Multimodal Embeddings.
    Official docs () report that the way to make embeddings out of images is to encode them as base64 data URLs and pass them as input to the embed endpoint.

    ```python
    import cohere
    import requests
    import base64

    co = cohere.ClientV2()

    image = requests.get("https://cohere.com/favicon-32x32.png")
    stringified_buffer = base64.b64encode(image.content).decode("utf-8")
    content_type = image.headers["Content-Type"]
    image_base64 = f"data:{content_type};base64,{stringified_buffer}"

    image_inputs = [
        {
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": image_base64}
                }
            ]
        }
    ]

    response = co.embed(
        model="embed-v4.0",
        input_type="image",
        embedding_types=["float"],
        inputs=image_inputs
    )

    print(response)
    ```

    """

    def __init__(self, api_key: str, model: str = "embed-v4.0"):
        """
        Initializes the Cohere client.
        
        Args:
            api_key: The Cohere API key.
            model: The model to use. 'embed-v4.0' 
                   support multimodal inputs in ClientV2.
        """
        self.client = cohere.ClientV2(api_key=api_key)
        self.async_client = cohere.AsyncClientV2(api_key=api_key)
        self.model = model

    def _image_to_base64_data_url(self, image_path: Union[str, Path]) -> str:
        """Converts an image to a base64 data URL."""
        path = Path(image_path)
        extension = path.suffix.lower()[1:]
        # map common extensions to mime types
        mime_type = f"image/{extension}"
        if extension == "jpg":
            mime_type = "image/jpeg"
            
        with open(path, "rb") as f:
            encoded_string = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime_type};base64,{encoded_string}"

    def embed_image(self, image_path: Union[str, Path]) -> List[float]:
        """Generates an embedding for an image."""
        return self.embed_batch([image_path])[0]

    async def _embed_sub_batch(self, image_paths: List[Union[str, Path]]) -> List[List[float]]:
        """Internal helper to handle a single API call for a sub-batch."""
        inputs = []
        for path in image_paths:
            base64_url = self._image_to_base64_data_url(path)
            inputs.append({
                "content": [
                    {"type": "image_url", "image_url": {"url": base64_url}}
                ]
            })
        
        response = await self.async_client.embed(
            model=self.model,
            inputs=inputs,
            input_type="image",
            embedding_types=["float"]
        )
        return response.embeddings.float

    def embed_batch(self, image_paths: List[Union[str, Path]], batch_size: int = 8, num_parallel_calls: int = 2) -> List[List[float]]:
        """
        Generates embeddings for a list of images using parallel async calls.
        """
        if not image_paths:
            return []

        # Split paths into sub-batches based on batch_size
        sub_batches = [image_paths[i:i + batch_size] for i in range(0, len(image_paths), batch_size)]
        
        async def run_parallel():
            semaphore = asyncio.Semaphore(num_parallel_calls)
            
            async def sem_task(sub_batch):
                async with semaphore:
                    return await self._embed_sub_batch(sub_batch)

            tasks = [sem_task(sb) for sb in sub_batches]
            results = await asyncio.gather(*tasks)
            # Flatten the list of lists into a single list of embeddings
            return [emb for sub_result in results for emb in sub_result]

        # Run the async loop in the current environment
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # For environments like Jupyter/Colab where the loop is already running
                import nest_asyncio
                nest_asyncio.apply()
            return loop.run_until_complete(run_parallel())
        except Exception as e:
            return asyncio.run(run_parallel())

    def embed_text(self, text: str, search_mode: str = "search_document") -> List[float]:
        """Generates an embedding for text."""
        response = self.client.embed(
            model=self.model,
            texts=[text],
            input_type=search_mode,
            embedding_types=["float"]
        )
        return response.embeddings.float[0]

    def embed_text_search(self, text: str) -> List[float]:
        """Generates an embedding for text."""
        return self.embed_text(text, search_mode="search_document")

    def embed_text_query(self, text: str) -> List[float]:
        """Generates an embedding for text."""
        return self.embed_text(text, search_mode="search_query")