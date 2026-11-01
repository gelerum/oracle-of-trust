import json
from pathlib import Path

from oracle.adapters import SignedHttpAdapter, SignedFileAdapter, UnavailableAdapter
from oracle.configuration import SourceDefinition
from oracle.models import Evidence


class AdapterFactories:
    """Register trusted adapter factories without changes to the gateway core."""

    def __init__(self):
        self.factories = {}
        self.register("signed-http", SignedHttpAdapter)
        self.register("signed-file", SignedFileAdapter)

    def register(self, name: str, factory):
        """Install a factory from trusted application code.

        Args:
            name: Adapter identifier used by source configuration.
            factory: Callable accepting a source definition.
        """
        if name in self.factories:
            raise ValueError("adapter factory already registered")
        self.factories[name] = factory

    def create(self, source: dict):
        """Instantiate an installed adapter.

        Args:
            source: Validated source definition.

        Returns:
            Adapter implementing the standard pull and push interface.
        """
        if source["adapter"] not in self.factories:
            raise ValueError("adapter is not installed")
        return self.factories[source["adapter"]](source)


class Registry:
    """Build a catalogue from source definitions and trusted adapter factories.

    Args:
        sources: Source definitions loaded from PostgreSQL or bootstrap config.
        signers: Optional public signer overrides for demo bootstrap.
        factories: Installed adapter factory registry.
    """

    def __init__(self, sources: list[dict], signers: dict | None = None,
                 factories: AdapterFactories | None = None):
        self.factories = factories or AdapterFactories()
        self.sources, self.adapters = {}, {}
        for source in sources:
            source = dict(source)
            if signers and source["sourceId"] in signers:
                source["signer"] = signers[source["sourceId"]]
            source = SourceDefinition.model_validate(source).model_dump()
            sid = source["sourceId"]
            if sid in self.sources:
                raise ValueError("duplicate source identifier")
            self.sources[sid] = source
            try:
                self.adapters[sid] = self.factories.create(source)
            except (KeyError, ValueError, OSError):
                self.adapters[sid] = UnavailableAdapter()

    def catalogue(self) -> list[dict]:
        """Return source metadata and the canonical evidence schema.

        Returns:
            Registered sources without private credentials.
        """
        return [dict(source, responseSchema=Evidence.model_json_schema()) for source in self.sources.values()]


def load_json(path: str):
    """Read a trusted JSON configuration file.

    Args:
        path: Configuration path.

    Returns:
        Parsed configuration.
    """
    return json.loads(Path(path).read_text())
