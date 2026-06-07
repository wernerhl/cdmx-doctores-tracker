from tracker.adapters.icasas import IcasasAdapter
from tracker.adapters.lamudi import LamudiAdapter
from tracker.adapters.vivanuncios import VivanunciosAdapter
from tracker.adapters.inmuebles24 import Inmuebles24Adapter
from tracker.adapters.propiedades import PropiedadesAdapter
from tracker.adapters.mudafy import MudafyAdapter

ADAPTERS = {
    "icasas": IcasasAdapter,
    "lamudi": LamudiAdapter,
    "vivanuncios": VivanunciosAdapter,
    "inmuebles24": Inmuebles24Adapter,
    "propiedades": PropiedadesAdapter,
    "mudafy": MudafyAdapter,
}
