from __future__ import annotations

from pathlib import Path

from agentry.config import ConfigStore
from agentry.models import Component, ComponentType, Source, SourceType, Target


def test_create_and_parse(project: Path):
    cfg = ConfigStore.load(project).parsed()
    assert cfg.version == 1
    assert cfg.targets == [Target.CLAUDE]
    assert cfg.sources == []


def test_roundtrip_preserves_comments(project: Path):
    path = ConfigStore.path_for(project)
    text = path.read_text()
    text = text.replace("targets:", "# my comment\ntargets:")
    path.write_text(text)

    store = ConfigStore.load(project)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path="../x"))
    store.save()

    assert "# my comment" in path.read_text()


def test_mutators(project: Path):
    store = ConfigStore.load(project)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path="../x"))
    store.add_component(Component(source="s", type=ComponentType.SKILL, name="a"))
    store.save()

    cfg = ConfigStore.load(project).parsed()
    assert cfg.source("s") is not None
    assert cfg.find_component("s/skill/a").enabled is True

    store = ConfigStore.load(project)
    assert store.set_enabled("s/skill/a", False)
    store.save()
    assert ConfigStore.load(project).parsed().find_component("s/skill/a").enabled is False

    store = ConfigStore.load(project)
    assert store.remove_component("s/skill/a")
    store.save()
    assert ConfigStore.load(project).parsed().find_component("s/skill/a") is None


def test_remove_source_drops_components(project: Path):
    store = ConfigStore.load(project)
    store.add_source(Source(name="s", type=SourceType.LOCAL, path="../x"))
    store.add_component(Component(source="s", type=ComponentType.SKILL, name="a"))
    store.save()

    store = ConfigStore.load(project)
    store.remove_source("s")
    store.save()
    cfg = ConfigStore.load(project).parsed()
    assert cfg.sources == []
    assert cfg.components == []
