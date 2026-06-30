"""HPXML generation + DOE Home Energy Score (HEScore) integration.

Pipeline:

    Audit data  ->  mapper.build_hes_model()  ->  HesBuilding (intermediate)
                ->  serializer.to_hpxml()      ->  HPXML XML string
                ->  hes_client                 ->  DOE HEScore API -> Score + Label

The intermediate ``HesBuilding`` model decouples our audit schema from HPXML
quirks and from the HEScore input requirements, so each layer can evolve
independently.
"""

from .hes_model import HesBuilding
from .mapper import build_hes_model
from .serializer import to_hpxml
from .gaps import compute_gaps, GapField

__all__ = [
    "HesBuilding",
    "build_hes_model",
    "to_hpxml",
    "compute_gaps",
    "GapField",
]
