"""Merge strategy — inject a fragment of named entries into a target tool's config file.

Contract: a source fragment (``mcp/<name>.json`` or ``hooks/<name>.json``) is always a
JSON **object of named entries**. Each top-level key is merged under the target's
pointer (e.g. ``mcpServers``); agentry records exactly those keys so they can be
removed later without disturbing hand-added entries.

Example ``mcp/github.json``::

    { "github": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"] } }

**Destination format.** The *source* fragment is always JSON, but the *destination*
config file may be JSON or TOML — chosen by the destination file's extension. A ``.toml``
destination (e.g. Codex's ``.codex/config.toml`` under ``[mcp_servers]``) is read and
written with :mod:`tomlkit`, preserving the user's comments, key order, and unrelated
settings exactly as ``ruamel`` does for ``.agentry.yml``. Everything else is JSON. The
named-entry merge contract and key-scoped reversibility are identical for both.

**Wrapped fragments.** Real-world plugin/MCP files often ship their entries already
*wrapped* under the section name they target — e.g. a Claude Code plugin's
``hooks.json`` is ``{ "description": ..., "hooks": { "Stop": [...] } }`` and an
``.mcp.json`` is ``{ "mcpServers": { ... } }``. :func:`select_entries` unwraps such a
fragment (using the destination's :attr:`~agentry.spec.MergeDest.wrapper_keys`) so
the real named entries — not the wrapper or sibling metadata like ``description`` —
are what gets merged. An already-flat fragment is used as-is.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit
import tomlkit.exceptions

from ..models import OwnedEntry
from ..progname import prog
from ..spec import MergeDest
from ._paths import require_confined


def load_fragment(artifact: Path) -> dict:
    data = json.loads(artifact.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{artifact} must be a JSON object of named entries")
    return data


def select_entries(fragment: dict, dest: MergeDest) -> dict:
    """Return the named entries to merge, unwrapping a canonical wrapper if present.

    A fragment may ship its entries wrapped under the section name it targets
    (e.g. ``{"hooks": {...}}`` or ``{"mcpServers": {...}}``), optionally alongside
    metadata like a top-level ``description``. When a wrapper key (see
    :attr:`MergeDest.wrapper_keys`) is present with an object value, those inner
    entries are the real payload and any sibling keys are ignored. An already-flat
    fragment is returned unchanged.
    """
    for key in dest.wrapper_keys:
        inner = fragment.get(key)
        if isinstance(inner, dict):
            return inner
    return fragment


def _is_toml(path: Path) -> bool:
    return path.suffix == ".toml"


class MergeTargetError(ValueError):
    """A merge destination exists but cannot be parsed, so agentry leaves it untouched.

    Raised before anything is written: an unparseable file (truncated JSON, JSONC
    comments, invalid TOML, a non-object root) can't be merged into without losing the
    user's content, so the caller warns and skips that destination instead.
    """


def _read_doc(path: Path, label: str | None = None) -> MutableMapping:
    """Read a config file as a mutable mapping (a tomlkit document for ``.toml``).

    A missing/empty file yields an empty document of the right kind so a later write
    produces the correct format. Both backends return a ``MutableMapping``, so the merge
    logic below is format-agnostic. A file that exists but can't be parsed raises
    :class:`MergeTargetError` naming ``label`` (the project-relative path).
    """
    name = label or str(path)
    if not path.is_file():
        return tomlkit.document() if _is_toml(path) else {}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise MergeTargetError(f"{name} is not UTF-8 text — left unchanged") from exc
    if _is_toml(path):
        try:
            return tomlkit.parse(text)
        except tomlkit.exceptions.TOMLKitError as exc:
            raise MergeTargetError(
                f"{name} is not valid TOML ({exc}) — left unchanged; fix it, then re-run sync"
            ) from exc
    if not text.strip():
        return {}
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MergeTargetError(
            f"{name} is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg}) — "
            "left unchanged; fix it (comments and trailing commas are not supported), "
            "then re-run sync"
        ) from exc
    if not isinstance(doc, dict):
        raise MergeTargetError(f"{name} is not a JSON object — left unchanged")
    return doc


def _serialize(path: Path, doc: MutableMapping) -> str:
    """Render ``doc`` in the format chosen by ``path``'s extension.

    JSON is written with ``ensure_ascii=False`` so a user's non-ASCII values (``"héllo"``)
    round-trip verbatim instead of being rewritten as ``\\u`` escapes.
    """
    if _is_toml(path):
        text = tomlkit.dumps(doc)
        return text if text.endswith("\n") else text + "\n"
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _write_doc(path: Path, doc: MutableMapping, *, before: str | None) -> None:
    """Write ``doc`` unless it is unchanged from the ``before`` snapshot.

    ``before`` is the serialization of the document as read (``None`` when the file did not
    exist). Comparing serializations — not raw file bytes — means a merge that changes
    nothing leaves the user's file untouched (bytes, formatting, and mtime), even when its
    formatting differs from ours.
    """
    text = _serialize(path, doc)
    if before is not None and text == before:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def install_merge(root: Path, dest: MergeDest, fragment: dict) -> list[str]:
    """Merge ``fragment`` into the target config; return the owned keys."""
    path = require_confined(root, dest.file)
    doc = _read_doc(path, dest.file)
    before = _serialize(path, doc) if path.is_file() else None
    section = doc.get(dest.pointer)
    if not isinstance(section, MutableMapping):
        doc[dest.pointer] = {}
        # Re-fetch: tomlkit converts the assigned dict into a live Table, so the local
        # must come from the document (a stale plain-dict reference wouldn't persist).
        section = doc[dest.pointer]
    for key, value in fragment.items():
        section[key] = value
    _write_doc(path, doc, before=before)
    return list(fragment.keys())


def remove_merge(root: Path, dest: MergeDest, keys: list[str]) -> bool:
    """Remove agentry-owned ``keys`` from the target config. Leaves the rest intact."""
    path = require_confined(root, dest.file)
    if not path.is_file():
        return False
    doc = _read_doc(path, dest.file)
    section = doc.get(dest.pointer)
    if not isinstance(section, MutableMapping):
        return False
    before = _serialize(path, doc)
    removed = False
    for key in keys:
        if key in section:
            del section[key]
            removed = True
    if not removed:
        return False  # nothing of ours here — never rewrite (or prune) the user's file
    if not section:
        doc.pop(dest.pointer, None)
    _write_doc(path, doc, before=before)
    return removed


def merge_state(root: Path, dest: MergeDest, keys: list[str]) -> str:
    """Drift check: ``"ok"`` if every owned key is present, else ``"missing"``."""
    path = require_confined(root, dest.file)
    if not path.is_file():
        return "missing"
    section = _read_doc(path, dest.file).get(dest.pointer)
    if not isinstance(section, MutableMapping):
        return "missing"
    return "ok" if all(k in section for k in keys) else "missing"


# -- ownership-aware merging (ADR-001, named mode) ----------------------------------------
#
# The functions below work on an in-memory document held by a :class:`Documents` cache, so
# reconcile reads each destination once, evaluates every component against it, and writes it
# at most once. Ownership is proven by a content fingerprint recorded in the manifest
# (:class:`~agentry.models.OwnedEntry`): agentry only updates or removes a key whose on-disk
# value still matches what it wrote. Keys it did not write are never touched.


def keep_hint(ref: str, arg: str) -> str:
    """The ``agentry keep`` invocation that hands an entry over to the user (backticked).

    ``arg`` is the keep: entry — ``<target>:<key>`` or a bare ``<key>`` (see
    :meth:`MergeContext.keep_arg`).
    """
    return f"`{prog()} keep {ref} {arg}`"


def fingerprint(value: object) -> str:
    """Stable content hash of a merged value (tomlkit items are unwrapped first)."""
    unwrap = getattr(value, "unwrap", None)
    plain = unwrap() if callable(unwrap) else value
    canon = json.dumps(plain, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


class Documents:
    """Per-sync cache of parsed merge destinations: each file read once, written once.

    A parse failure is cached too, so every component targeting the same broken file gets
    the same :class:`MergeTargetError` without re-reading it.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._docs: dict[str, tuple[Path, MutableMapping, str | None]] = {}
        self._errors: dict[str, MergeTargetError] = {}

    def get(self, file: str) -> MutableMapping:
        if file in self._errors:
            raise self._errors[file]
        if file not in self._docs:
            path = require_confined(self.root, file)
            try:
                doc = _read_doc(path, file)
            except MergeTargetError as exc:
                self._errors[file] = exc
                raise
            before = _serialize(path, doc) if path.is_file() else None
            self._docs[file] = (path, doc, before)
        return self._docs[file][1]

    def flush(self) -> None:
        """Write every document that changed (unchanged files are left byte-identical)."""
        for path, doc, before in self._docs.values():
            _write_doc(path, doc, before=before)


_ABSENT = object()


def _json_type(value: object) -> str:
    if isinstance(value, list):
        return "list"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "null" if value is None else type(value).__name__


@dataclass
class NamedPlan:
    """What applying one component's fragment to a document would do.

    ``refused`` means nothing is written for the component (atomic per destination); its
    previous record, if any, is kept so it stays owned. ``entries`` is the ownership map to
    record when the plan is committed.
    """

    entries: dict[str, OwnedEntry] = field(default_factory=dict)
    writes: dict[str, object] = field(default_factory=dict)
    created: bool = False
    updated: bool = False
    refused: bool = False
    warnings: list[str] = field(default_factory=list)
    #: The W2 subset of ``warnings`` (kept entries that differ from their source) — the
    #: CLI summarizes these; doctor lists them per key.
    drift_warnings: list[str] = field(default_factory=list)
    note: str | None = None  # why the component isn't installed (status row note)
    kept: list[str] = field(default_factory=list)  # keys present on disk the user keeps
    #: Keys refused one at a time on an installed component (W1/W1b/W5 after install), with
    #: the status note for each; the component's other keys still apply.
    key_notes: dict[str, str] = field(default_factory=dict)
    #: Kept keys with no entry on disk here: never created, but reported (status missing).
    missing_notes: dict[str, str] = field(default_factory=dict)
    #: The warnings for ``missing_notes`` (summarized as kind "kept" by the CLI).
    kept_missing_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MergeContext:
    """Labels for warnings: who is merging what, where."""

    ref: str
    target: str
    file: str
    pointer: str
    #: Keys whose keep hint is the bare key: they drifted on *every* target the component
    #: writes them to, so keeping them everywhere is exactly right. Any other key's hint
    #: names this target (``<target>:<key>``) so keep never freezes a healthy target.
    bare: frozenset[str] = frozenset()

    def at(self, key: str) -> str:
        return f"'{self.pointer}.{key}' in {self.file}"

    def keep_arg(self, key: str) -> str:
        return key if key in self.bare else f"{self.target}:{key}"

    def keep(self, key: str) -> str:
        return keep_hint(self.ref, self.keep_arg(key))


def _users_entry_differs(ctx: MergeContext, key: str, rec: OwnedEntry) -> tuple[str, str]:
    """(warning, note) for a pre-existing user entry that no longer matches the source."""
    if rec.kept:  # it became the user's through keep:, which has since been removed
        return (
            f"{ctx.ref}: {ctx.at(key)} is your entry (no longer in keep:) — left unchanged. "
            f"Delete it and re-run sync to take {ctx.ref}'s version.",
            f"{ctx.at(key)} is your entry (no longer in keep:) — delete it to take "
            f"{ctx.ref}'s version",
        )
    return (
        f"{ctx.ref}: {ctx.at(key)} is your entry (it matched {ctx.ref} earlier, but upstream "
        f"has changed) — keeping yours. Delete it to take {ctx.ref}'s version, or run "
        f"{ctx.keep(key)} to keep it.",
        f"{ctx.at(key)} is your entry and differs from upstream — delete it to take "
        f"{ctx.ref}'s version, or run {ctx.keep(key)} to keep it",
    )


def _plan_owned(
    plan: NamedPlan,
    ctx: MergeContext,
    key: str,
    desired: object,
    rec: OwnedEntry,
    have: str,
    want: str,
) -> None:
    """A key agentry recorded: update it if untouched, keep it (W2) if the user edited it."""
    if have != rec.fingerprint:
        # The user edited what we wrote (or a v1 record never proved it): theirs now. Keep
        # the record so status reports drift; never write over it.
        plan.entries[key] = rec
        if rec.fingerprint is None:  # v1 record, value never proven to be agentry's
            w2 = (
                f"{ctx.ref}: {ctx.at(key)} was recorded by an older agentry and differs from "
                "the current source — keeping it. If you didn't edit it, delete it and re-run "
                f"sync to get the current version, or run {ctx.keep(key)} to keep yours."
            )
        else:
            w2 = (
                f"{ctx.ref}: {ctx.at(key)} was edited after agentry installed it — keeping "
                "your version. Delete it and re-run sync to restore agentry's, or run "
                f"{ctx.keep(key)} to keep yours."
            )
        plan.warnings.append(w2)
        plan.drift_warnings.append(w2)
        return
    plan.entries[key] = OwnedEntry(mode="named", fingerprint=want)
    if have != want:
        plan.writes[key] = desired
        plan.updated = True


def plan_named(
    doc: MutableMapping,
    ctx: MergeContext,
    fragment: dict,
    owned: dict[str, OwnedEntry],
    claimed: Callable[[str], str | None],
    kept: frozenset[str] = frozenset(),
    *,
    installed: bool = False,
) -> NamedPlan:
    """Plan merging ``fragment`` (named mode) without mutating ``doc``.

    A conflict (W1 the user's entry, W1b an adopted entry upstream changed, W5 another
    component's) refuses the **whole** component on its first install into a destination —
    no half-installed MCP server or hook set. Once ``installed`` (it already has a record
    there), only the offending key is refused and the other keys keep updating (ADR-001
    Rev 4). W3 (non-object section) always refuses the whole destination.

    ``owned`` is this component's recorded ownership for the destination; ``claimed(key)``
    names another live component that owns ``key`` there, if any. ``kept`` keys (the
    component's ``keep:``) are the user's: recorded as pre-existing, never written.
    """
    plan = NamedPlan()
    section = doc.get(ctx.pointer, _ABSENT)
    if section is not _ABSENT and not isinstance(section, MutableMapping):
        plan.refused = True
        plan.warnings.append(
            f"{ctx.ref}: '{ctx.pointer}' in {ctx.file} is a {_json_type(section)}, not an "
            "object — not merged. Fix the file by hand, then re-run sync."
        )
        plan.note = f"'{ctx.pointer}' in {ctx.file} is not an object — fix the file by hand"
        return plan
    current: MutableMapping = section if isinstance(section, MutableMapping) else {}

    def refuse(key: str, warning: str, note: str, *, drift: bool = False) -> None:
        plan.warnings.append(warning)
        if drift:
            plan.drift_warnings.append(warning)
        if installed:
            plan.key_notes[key] = note
            rec = owned.get(key)
            if rec is not None:
                plan.entries[key] = rec  # still the record it was; nothing written
        else:
            plan.refused = True
            plan.note = plan.note or note

    for key, desired in fragment.items():
        if key in kept:
            # The user keeps this key: theirs from now on — no write, no warning. Recorded as
            # pre-existing (kept-origin) so removal never touches it and un-keeping
            # reclaims nothing.
            if key in current:
                plan.entries[key] = OwnedEntry(
                    mode="named",
                    fingerprint=fingerprint(current[key]),
                    preexisting=True,
                    kept=True,
                )
                plan.kept.append(key)
            else:
                # Never created while kept — but not silent (e.g. a target added after a
                # bare keep, or the user deleted their kept entry).
                plan.missing_notes[key] = (
                    f"kept, but no entry on {ctx.target} — add yours, remove it from `keep:` "
                    f"to let agentry install it, or narrow keep to `<target>:<key>`"
                )
                w = (
                    f"{ctx.ref}: {ctx.at(key)} — kept, but no entry on {ctx.target}; nothing "
                    "installed. Add yours, remove it from `keep:` to let agentry install it, "
                    "or narrow keep to `<target>:<key>`."
                )
                plan.warnings.append(w)
                plan.kept_missing_warnings.append(w)
            continue
        want = fingerprint(desired)
        rec = owned.get(key)
        if key not in current:
            # Absent: ours to create (or re-create after the user deleted our entry).
            plan.writes[key] = desired
            plan.entries[key] = OwnedEntry(mode="named", fingerprint=want)
            plan.created = True
            continue
        have = fingerprint(current[key])
        if rec is not None and rec.preexisting:
            if have == want:
                plan.entries[key] = rec
            else:
                warning, note = _users_entry_differs(ctx, key, rec)
                refuse(key, warning, note, drift=True)
        elif rec is not None:
            _plan_owned(plan, ctx, key, desired, rec, have, want)
        elif (other := claimed(key)) is not None:
            refuse(
                key,
                f"{ctx.ref}: {ctx.at(key)} is already provided by {other} — skipped. Disable "
                "one of them or rename the entry.",
                f"{ctx.at(key)} is already provided by {other}",
            )
        elif have == want:
            plan.entries[key] = OwnedEntry(mode="named", fingerprint=want, preexisting=True)
            plan.warnings.append(
                f"{ctx.ref}: {ctx.file} already has an identical '{ctx.pointer}.{key}' (not "
                "added by agentry) — using yours; agentry will never remove it."
            )
        else:
            what = f"'{key}'" if installed else ctx.ref
            refuse(
                key,
                f"{ctx.ref}: {ctx.file} already has '{ctx.pointer}.{key}' (not added by "
                f"agentry) — left unchanged; {what} was not merged into {ctx.target}. Rename or "
                f"remove your entry, or disable {ctx.ref}, then re-run sync.",
                f"{ctx.file} already has '{ctx.pointer}.{key}' (not added by agentry) — rename "
                f"or remove your entry, or disable {ctx.ref}",
            )
    if plan.refused:
        plan.writes.clear()
        plan.created = plan.updated = False
        plan.key_notes.clear()
        plan.missing_notes.clear()
    return plan


def commit_named(doc: MutableMapping, pointer: str, plan: NamedPlan) -> None:
    """Apply a non-refused plan's writes to ``doc``."""
    if plan.refused or not plan.writes:
        return
    section = doc.get(pointer)
    if not isinstance(section, MutableMapping):
        doc[pointer] = {}
        section = doc[pointer]  # tomlkit turns the dict into a live Table; re-fetch it
    for key, value in plan.writes.items():
        section[key] = value


def remove_named(
    doc: MutableMapping,
    ctx: MergeContext,
    entries: dict[str, OwnedEntry],
    unverified: Callable[[str], str] | None = None,
) -> tuple[bool, list[str]]:
    """Remove the keys agentry owns (fingerprint still matches); return (removed, warnings).

    Pre-existing entries are never removed; an entry the user edited is left in place, and
    so is one whose ownership was never proven (``fingerprint=None``, from a v1 manifest).
    ``unverified(key)`` overrides the warning for the latter (e.g. W6 for link+merge).
    """
    section = doc.get(ctx.pointer)
    if not isinstance(section, MutableMapping):
        return False, []
    removed = False
    warnings: list[str] = []
    for key, rec in entries.items():
        if rec.preexisting or key not in section:
            continue
        if rec.fingerprint is None:
            warnings.append(
                unverified(key)
                if unverified is not None
                else f"{ctx.ref}: {ctx.at(key)} could not be verified as agentry's (recorded "
                "by an older agentry) — left in place on removal; delete it by hand if unused, "
                f"or run {ctx.keep(key)} to keep it as yours."
            )
            continue
        if fingerprint(section[key]) != rec.fingerprint:
            warnings.append(
                f"{ctx.ref}: {ctx.at(key)} was edited after install — left in place on "
                f"removal; delete it by hand if unused, or run {ctx.keep(key)} to "
                "keep it as yours."
            )
            continue
        del section[key]
        removed = True
    if removed and not section:
        doc.pop(ctx.pointer, None)
    return removed, warnings


def migrate_named(
    doc: MutableMapping, pointer: str, keys: list[str], desired: dict | None
) -> dict[str, OwnedEntry]:
    """Ownership for a legacy v1 record, which asserted every key in ``keys``.

    A v1 record proves nothing about *what* agentry wrote, so a key is owned only when its
    current value equals what the component installs (``desired``). Anything else — a value
    the user replaced, a hook array they appended to, a key absent on disk, or a component
    whose fragment can't be re-derived — gets ``fingerprint=None``: unproven, so it is never
    overwritten or removed, and reports as drift.
    """
    section = doc.get(pointer)
    current: MutableMapping = section if isinstance(section, MutableMapping) else {}
    out: dict[str, OwnedEntry] = {}
    for key in keys:
        fp: str | None = None
        if key in current and desired is not None and key in desired:
            have = fingerprint(current[key])
            if fingerprint(desired[key]) == have:
                fp = have
        out[key] = OwnedEntry(mode="named", fingerprint=fp)
    return out


def drifted_keys(
    doc: MutableMapping,
    pointer: str,
    fragment: dict,
    owned: dict[str, OwnedEntry],
    kept: frozenset[str] = frozenset(),
) -> list[tuple[str, bool]]:
    """Keys agentry recorded whose on-disk value it can't vouch for: ``(key, unproven)``.

    ``unproven`` is True for a v1 record never proven to be agentry's (``fingerprint=None``)
    and False for an entry agentry wrote that was edited by hand since.
    """
    section = doc.get(pointer)
    current: MutableMapping = section if isinstance(section, MutableMapping) else {}
    out: list[tuple[str, bool]] = []
    for key in fragment:
        rec = owned.get(key)
        if rec is None or rec.preexisting or key in kept or key not in current:
            continue
        if rec.fingerprint is None or rec.fingerprint != fingerprint(current[key]):
            out.append((key, rec.fingerprint is None))
    return out


def named_state(
    doc: MutableMapping,
    ctx: MergeContext,
    fragment: dict,
    owned: dict[str, OwnedEntry],
    claimed: Callable[[str], str | None],
    kept: frozenset[str] = frozenset(),
    *,
    installed: bool = False,
) -> tuple[str, str | None]:
    """Read-only drift check: ``(state, note)``; state ``ok`` | ``ok (kept)`` | ``missing`` |
    ``drift``.

    A refused install (user's entry, non-object section, duplicate) is ``missing`` with a
    note saying why; an owned entry the user edited is ``drift``; a component whose only
    differences are keys it keeps is ``ok (kept)``.
    """
    plan = plan_named(doc, ctx, fragment, owned, claimed, kept, installed=installed)
    if plan.refused:
        return "missing", plan.note
    if plan.key_notes:  # a key refused on an installed component: the rest still applies
        return "drift", next(iter(plan.key_notes.values()))
    if plan.missing_notes:  # a kept key with no entry here: reported, never created
        return "missing", next(iter(plan.missing_notes.values()))
    section = doc.get(ctx.pointer)
    current: MutableMapping = section if isinstance(section, MutableMapping) else {}
    for key in fragment:
        rec = owned.get(key)
        if (
            key not in kept
            and key in current
            and rec is not None
            and not rec.preexisting
            and rec.fingerprint != fingerprint(current[key])
        ):
            if rec.fingerprint is None:
                return "drift", (
                    "recorded by an older agentry; value differs from the current source — if "
                    "you didn't edit it, delete the entry and run sync to get the current "
                    f"version, or run {ctx.keep(key)} to keep yours"
                )
            return "drift", (
                f"edited by hand — delete the entry and run `{prog()} sync` to restore it, or "
                f"run {ctx.keep(key)} to keep yours"
            )
    if plan.created:
        return "missing", None
    if plan.updated:
        return "drift", None
    return ("ok (kept)" if plan.kept else "ok"), None
