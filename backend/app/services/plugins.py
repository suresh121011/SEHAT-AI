"""Semantic Kernel plugin stub. Extraction/summary functions are added in Phase 6."""

from semantic_kernel.functions import kernel_function


class SehatPlugin:
    @kernel_function(name="plugin_status", description="Reports that the SEHAT plugin is registered.")
    def plugin_status(self) -> str:
        return "sehat plugin registered"
