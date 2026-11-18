"""calibre-news engine: run calibre news recipes anywhere Python runs (desktop, Android/Chaquopy).

Produces EPUB files from news recipes originally written for calibre.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .recipe import BasicNewsRecipe
from .runner import (
    RecipeInfo,
    RunResult,
    list_recipes,
    list_recipes_dict,
    load_recipe_class,
    run_recipe,
    run_recipe_dict,
)

__all__ = [
    "BasicNewsRecipe",
    "RecipeInfo",
    "RunResult",
    "list_recipes",
    "list_recipes_dict",
    "load_recipe_class",
    "run_recipe",
    "run_recipe_dict",
    "__version__",
]
