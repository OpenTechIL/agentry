"""Read/write ``.agentry.yml`` with comment-preserving round trips.

The store keeps the raw ruamel document (so user comments and key order survive
edits) and exposes a validated :class:`~agentry.models.Config` view for logic.
All mutations edit the raw document in place.
"""

from __future__ import annotations

import io
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .models import Component, ComponentType, Config, ProfileRule, Registry, Source, SourceType

CONFIG_NAME = ".agentry.yml"
LOCK_NAME = ".agentry.lock"
STORE_DIR = ".agentry"
MANIFEST_NAME = ".manifest.json"


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.default_flow_style = False
    return y


class ConfigStore:
    """Owns the on-disk ``.agentry.yml`` for a project root."""

    def __init__(self, root: Path, doc: CommentedMap):
        self.root = root
        self.doc = doc

    # -- construction -----------------------------------------------------

    @classmethod
    def path_for(cls, root: Path) -> Path:
        return root / CONFIG_NAME

    @classmethod
    def exists(cls, root: Path) -> bool:
        return cls.path_for(root).is_file()

    @classmethod
    def load(cls, root: Path) -> ConfigStore:
        path = cls.path_for(root)
        if not path.is_file():
            raise FileNotFoundError(f"No {CONFIG_NAME} found in {root}. Run `agy init` first.")
        doc = _yaml().load(path.read_text(encoding="utf-8")) or CommentedMap()
        return cls(root, doc)

    @classmethod
    def create(cls, root: Path, targets: list[str]) -> ConfigStore:
        doc = CommentedMap()
        doc["version"] = 1
        doc["targets"] = CommentedSeq(targets)
        doc["sources"] = CommentedSeq()
        doc["components"] = CommentedSeq()
        doc.yaml_set_start_comment(
            "agentry — AI agent dependencies for this project.\n"
            "Declare sources and components here; run `agy sync` to install.\n"
        )
        return cls(root, doc)

    # -- validated view ---------------------------------------------------

    def parsed(self) -> Config:
        return Config.model_validate(_plain(self.doc))

    # -- mutation helpers -------------------------------------------------

    def _seq(self, key: str) -> CommentedSeq:
        if key not in self.doc or self.doc[key] is None:
            self.doc[key] = CommentedSeq()
        return self.doc[key]

    def add_source(self, source: Source) -> None:
        sources = self._seq("sources")
        if any(s.get("name") == source.name for s in sources):
            raise ValueError(f"source '{source.name}' already exists")
        item = CommentedMap()
        item["name"] = source.name
        item["type"] = source.type.value
        if source.type is SourceType.GIT:
            item["url"] = source.url
            item["ref"] = source.ref
        else:
            item["path"] = source.path
        if source.subdir:
            item["subdir"] = source.subdir
        sources.append(item)

    def remove_source(self, name: str) -> bool:
        sources = self._seq("sources")
        before = len(sources)
        kept = [s for s in sources if s.get("name") != name]
        sources[:] = kept
        # Also drop components that belonged to it.
        comps = self._seq("components")
        comps[:] = [c for c in comps if c.get("source") != name]
        return len(sources) != before

    def add_repository(self, registry: Registry) -> None:
        repos = self._seq("repositories")
        if any(r.get("name") == registry.name for r in repos):
            raise ValueError(f"repository catalog '{registry.name}' already exists")
        item = CommentedMap()
        item["name"] = registry.name
        item["location"] = registry.location
        repos.append(item)

    def remove_repository(self, name: str) -> bool:
        repos = self._seq("repositories")
        before = len(repos)
        repos[:] = [r for r in repos if r.get("name") != name]
        return len(repos) != before

    def merge_target_profiles(self, profiles: dict[str, dict[ComponentType, ProfileRule]]) -> bool:
        """Merge per-target profile rules into ``target_profiles``, never clobbering existing.

        Keyed by ``target -> ComponentType.value -> rule``. A rule is written only when that
        ``(target, type)`` slot is absent, so a user-customized profile (or a re-add of the
        same repo) is left untouched. Returns True if anything was written.
        """
        if not profiles:
            return False
        if "target_profiles" not in self.doc or self.doc["target_profiles"] is None:
            self.doc["target_profiles"] = CommentedMap()
        root = self.doc["target_profiles"]
        changed = False
        for target, rules in profiles.items():
            tmap = root.setdefault(target, CommentedMap())
            for ctype, rule in rules.items():
                key = ctype.value if isinstance(ctype, ComponentType) else str(ctype)
                if key in tmap:
                    continue  # respect an existing/user-customized rule
                tmap[key] = _profile_rule_map(rule)
                changed = True
        return changed

    def add_component(self, comp: Component) -> None:
        comps = self._seq("components")
        if any(_comp_ref(c) == comp.ref for c in comps):
            return  # already declared; idempotent
        item = CommentedMap()
        item["source"] = comp.source
        item["type"] = comp.type.value
        item["name"] = comp.name
        item["enabled"] = comp.enabled
        if comp.targets is not None:
            item["targets"] = CommentedSeq(comp.targets)
        if comp.path is not None:
            item["path"] = comp.path
        if comp.generate is not None:
            gen = CommentedMap()
            if comp.generate.setup:
                gen["setup"] = CommentedSeq(CommentedSeq(cmd) for cmd in comp.generate.setup)
            gen["command"] = CommentedSeq(comp.generate.command)
            gen["produces"] = CommentedSeq(comp.generate.produces)
            item["generate"] = gen
        comps.append(item)

    def remove_component(self, ref: str) -> bool:
        comps = self._seq("components")
        before = len(comps)
        comps[:] = [c for c in comps if _comp_ref(c) != ref]
        return len(comps) != before

    def set_enabled(self, ref: str, enabled: bool) -> bool:
        for c in self._seq("components"):
            if _comp_ref(c) == ref:
                c["enabled"] = enabled
                return True
        return False

    # -- persistence ------------------------------------------------------

    def dumps(self) -> str:
        buf = io.StringIO()
        _yaml().dump(self.doc, buf)
        return buf.getvalue()

    def save(self) -> None:
        # Validate before writing so we never persist a broken config.
        self.parsed()
        self.path_for(self.root).write_text(self.dumps(), encoding="utf-8")


def _comp_ref(c: dict) -> str:
    return f"{c.get('source')}/{c.get('type')}/{c.get('name')}"


def _profile_rule_map(rule: ProfileRule) -> CommentedMap:
    """Serialize a :class:`ProfileRule` to a CommentedMap of its set (non-None) fields."""
    item = CommentedMap()
    item["strategy"] = rule.strategy.value
    for field in ("dest", "file", "pointer", "rewrite_from", "rewrite_to"):
        value = getattr(rule, field)
        if value is not None:
            item[field] = value
    return item


def _plain(obj):
    """Recursively convert ruamel CommentedMap/Seq into plain dict/list."""
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj
