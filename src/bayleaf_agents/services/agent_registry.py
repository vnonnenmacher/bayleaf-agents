import importlib, inspect, pkgutil, re
from typing import Dict, Type
from ..agents.base_agent import BaseAgent


def _slugify(name: str) -> str:
    s = name.replace("Agent", "")
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", s).lower()
    return s or name.lower()


def discover_agents() -> Dict[str, Type[BaseAgent]]:
    """
    Scans bayleaf_agents.agents package, imports each module, and
    returns {slug: AgentClass} for every concrete subclass of BaseAgent.
    """
    registry: Dict[str, Type[BaseAgent]] = {}

    pkg_name = "bayleaf_agents.agents"
    pkg = importlib.import_module(pkg_name)

    for m in pkgutil.iter_modules(pkg.__path__, pkg_name + "."):
        # skip the base module explicitly
        if m.name.endswith(".base_agent"):
            continue

        mod = importlib.import_module(m.name)
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if not issubclass(obj, BaseAgent) or obj is BaseAgent:
                continue
            slug = _slugify(obj.__name__)
            registry[slug] = obj

    return registry
