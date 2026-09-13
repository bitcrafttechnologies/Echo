"""Concrete standalone Medulla package for allowlisted web research."""

from medulla_node.adapters.web import DEFAULT_RESEARCH_DOMAINS, WebResearchAdapter
from web_medulla_node.config import WebNodeSettings, load_web_runtime

__all__ = ["DEFAULT_RESEARCH_DOMAINS", "WebNodeSettings", "WebResearchAdapter", "load_web_runtime"]
