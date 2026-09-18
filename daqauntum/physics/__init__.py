"""Physics knowledge: concepts, their relationships, and the units they live in.

A registry of what a quantity *is* - its dimension, its symbol, the relations
it takes part in - so the rest of the system can check that a calculation makes
physical sense rather than only arithmetic sense.

Nothing here computes anything or decides whether a result is correct. It
records what physics says about a quantity; `science.validation` uses that to
find contradictions. Neither is a truth oracle.
"""

from physics.concepts import (
    CONCEPTS,
    RELATIONS,
    Concept,
    ConceptError,
    Relation,
    concept,
    concepts_with_dimension,
    find_concept,
    relations_for,
)

__all__ = [
    "CONCEPTS",
    "RELATIONS",
    "Concept",
    "ConceptError",
    "Relation",
    "concept",
    "concepts_with_dimension",
    "find_concept",
    "relations_for",
]
