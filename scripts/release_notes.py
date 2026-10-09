#!/usr/bin/env python3
"""Build GitHub Release notes from the squash commits since the previous ``v*`` tag.

Usage::

    python scripts/release_notes.py [--to REF] [--from TAG]

``--to`` defaults to ``HEAD``. ``--from`` defaults to the newest exact ``vX.Y.Z`` tag
reachable from the first parent of ``--to``, so ``--to v1.3.1`` never picks ``v1.3.1``
itself. Tags that only start like a version (``v1.2.3.post1``, ``v2.0.0-rc1``) are
skipped and the search keeps walking back to the newest exact ``vX.Y.Z`` tag. An
explicit ``--from`` is honored as given, even when it is not an exact ``vX.Y.Z`` tag,
because the maintainer chose it. With no earlier tag the notes cover all history up to
``--to``. Markdown goes to
stdout; errors go to stderr with exit status 1. A shallow clone is refused, because
missing history and tags would silently change the range: check out with
``fetch-depth: 0``.

The release workflow writes the output to the file it passes as ``body_path``, and
the maintainer previews the same command on ``main`` before tagging. The output
depends only on the commit graph, tags and messages: no dates, no authors, fixed
7-character SHAs.

Sections:

* **Breaking changes**: every ``BREAKING CHANGE:`` footer (or its Conventional
  Commits synonym ``BREAKING-CHANGE:``), copied verbatim, under the commit subject and
  short SHA. A footer starts at a line beginning with the token and runs to the next
  ``BREAKING CHANGE:`` token, a standard git trailer line (``Co-authored-by:``,
  ``Signed-off-by:``, ``Refs #12``, see ``TRAILER_KEYS``) or the end of the message,
  so multi-paragraph migration steps stay whole. A Markdown heading after the token
  means it was body text, not a footer. The CodeRabbit release-notes marker
  (``<!-- This is an auto-generated comment: release notes by coderabbit.ai -->``)
  ends the message: it and everything after it are ignored, so a footer just before
  it is still the final paragraph. Lines inside fenced code blocks are never tokens,
  headings or the marker. Fences follow the CommonMark rules for top-level blocks
  (see ``FenceState``); fences inside block quotes or list items are not modeled.
  A ``type!:`` commit with no footer is listed with a warning, because its
  migration steps are missing.
* **Changes**: every commit subject (the squash title) with its short SHA, newest
  first, following first parents only.

Only ``git`` is required; there are no third-party dependencies.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

TAG_GLOB = "v[0-9]*"
# ``TAG_GLOB`` is only a prefilter for ``git describe``: it also matches ``v1.2.3.post1``.
# The default range starts at a tag that matches this exactly.
SEMVER_TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
SHORT_SHA_LEN = 7

_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"

BREAKING_TOKEN = re.compile(r"^BREAKING[ -]CHANGE:(?:[ \t]|$)")
BANG_SUBJECT = re.compile(r"^[A-Za-z]+(?:\([^)\n]*\))?!:")
# A possible fence line: up to 3 spaces of indentation, a run of 3+ backticks or
# tildes, then the rest of the line (the info string on an opening fence).
FENCE_LINE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^[ ]{0,3}#{1,6}(?:[ \t]|$)")
# CodeRabbit appends its walkthrough to the PR body (and so to the squash message) after
# this line. Everything from the marker on is generated text, never a footer.
CODERABBIT_MARKER = "<!-- This is an auto-generated comment: release notes by coderabbit.ai -->"

# Standard git / GitHub trailer keys that end a BREAKING CHANGE footer. Matched
# case-insensitively as ``Key: value`` or ``Key #ref``. Free-form lines such as
# ``Migration:`` or ``Read-only: ...`` stay part of the footer text.
TRAILER_KEYS = (
    "Acked-by",
    "Cc",
    "Change-Id",
    "Closes",
    "Co-authored-by",
    "Co-developed-by",
    "Fixes",
    "Helped-by",
    "Refs",
    "Reported-by",
    "Resolves",
    "Reviewed-by",
    "See-also",
    "Signed-off-by",
    "Suggested-by",
    "Tested-by",
)
TRAILER_LINE = re.compile(
    r"^(?:" + "|".join(re.escape(key) for key in TRAILER_KEYS) + r")(?:: | #)",
    re.IGNORECASE,
)


class ReleaseNotesError(RuntimeError):
    """A git command failed or a ref does not resolve."""


@dataclass(frozen=True)
class Commit:
    """One first-parent commit in the release range."""

    sha: str
    subject: str
    body: str
    breaking_footers: tuple[str, ...] = field(default=())

    @property
    def short_sha(self) -> str:
        return self.sha[:SHORT_SHA_LEN]

    @property
    def bang(self) -> bool:
        """True when the subject uses the Conventional Commits ``!`` marker."""
        return BANG_SUBJECT.match(self.subject) is not None


def normalize_newlines(text: str) -> str:
    """GitHub squash bodies can carry CRLF; release notes always use LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


class FenceState:
    """CommonMark fenced code block state for top-level lines (CommonMark 0.31.2, 4.5).

    * An opening fence is up to 3 spaces of indentation, then at least three backticks
      or three tildes. A backtick fence's info string may not contain a backtick, so
      an inline-code line such as ```` ```code``` ```` opens nothing; a tilde fence's
      info string may.
    * The block closes only on a line with up to 3 spaces of indentation, a run of the
      same character at least as long as the opening run, and nothing after it but
      spaces or tabs. Any other line, including a shorter run, the other character or
      a fence with an info string, is content.
    * An unclosed fence runs to the end of the message.

    Containers are not modeled: a fence on a ``>``-prefixed block quote line is not
    recognized, and a fence indented for a list item counts as top level.
    """

    def __init__(self) -> None:
        self.char: str | None = None
        self.length = 0

    def feed(self, line: str) -> bool:
        """Consume ``line``; True if it is an opening fence, fenced content or a closing fence."""
        match = FENCE_LINE.match(line)
        if self.char is None:
            if match is None:
                return False
            run, info = match.group(1), match.group(2)
            if run[0] == "`" and "`" in info:
                return False
            self.char, self.length = run[0], len(run)
            return True
        if match is not None:
            run, rest = match.group(1), match.group(2)
            if run[0] == self.char and len(run) >= self.length and rest.strip(" \t") == "":
                self.char, self.length = None, 0
        return True


def extract_breaking_footers(message: str) -> tuple[str, ...]:
    """Return every ``BREAKING CHANGE:`` footer in ``message``, verbatim.

    The first line (the subject) is never a footer. Each footer keeps its token and
    every following line up to the next footer token, git trailer line or end of the
    message; trailing blank lines are dropped. A Markdown heading outside a fence after
    the token means the token was body text, so that block is discarded. The CodeRabbit
    marker line (``CODERABBIT_MARKER``) outside a fence is a hard end of the message: it
    and everything after it are ignored. Quoted inside a fenced code block it is text.

    Fenced code blocks follow ``FenceState`` (CommonMark rules for top-level blocks):
    nested, mixed, shorter or info-string fences cannot end a block early, and an
    inline-code line such as ```` ```code``` ```` cannot open one, so neither can hide
    a real footer or expose a quoted one. Limitation: only top-level blocks are
    modeled. Fences inside block quotes are not recognized, a fence indented for a
    list item counts as top level, and HTML blocks are not tracked.

    Tokens, trailer keys and the marker must start at column 0: an indented
    ``BREAKING CHANGE:`` line is never a footer. Headings may be indented up to 3
    spaces, as in CommonMark.
    """
    lines = normalize_newlines(message).split("\n")[1:]
    footers: list[list[str]] = []
    current: list[str] | None = None
    fence = FenceState()
    for line in lines:
        if not fence.feed(line):
            if line.strip() == CODERABBIT_MARKER:
                break
            if BREAKING_TOKEN.match(line):
                current = [line]
                footers.append(current)
                continue
            if TRAILER_LINE.match(line):
                current = None
                continue
            if HEADING.match(line):
                # A Markdown section after the token: it was body text, not the final footer.
                if current is not None:
                    footers.remove(current)
                current = None
                continue
        if current is not None:
            current.append(line)
    return tuple("\n".join(block).rstrip() for block in footers)


def _git(args: Sequence[str], repo: Path | None) -> str:
    proc = subprocess.run(
        ["git", "-c", "log.showSignature=false", "-c", "core.quotePath=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit status {proc.returncode}"
        raise ReleaseNotesError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout


def resolve_commit(ref: str, repo: Path | None = None) -> str:
    """Return the full commit SHA ``ref`` points at, or raise ``ReleaseNotesError``."""
    try:
        return _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], repo).strip()
    except ReleaseNotesError:
        raise ReleaseNotesError(f"unknown revision: {ref}") from None


def previous_tag(to: str, repo: Path | None = None) -> str | None:
    """Newest exact ``vX.Y.Z`` tag reachable from the first parent of ``to``, or None.

    ``git describe`` finds the nearest ``TAG_GLOB`` tag; one that is not an exact
    ``vX.Y.Z`` (``SEMVER_TAG``), such as ``v1.2.3.post1``, is excluded and the search
    repeats, so it keeps walking back to the newest exact release tag.
    """
    parents = _git(["rev-list", "--parents", "-n", "1", resolve_commit(to, repo)], repo).split()
    if len(parents) < 2:
        return None
    excluded: list[str] = []
    while True:
        args = ["describe", "--tags", "--abbrev=0", "--first-parent", "--match", TAG_GLOB]
        for skipped in excluded:
            args += ["--exclude", skipped]
        try:
            tag = _git([*args, parents[1]], repo).strip()
        except ReleaseNotesError:
            return None
        if SEMVER_TAG.fullmatch(tag):
            return tag
        excluded.append(tag)


def list_commits(from_ref: str | None, to: str, repo: Path | None = None) -> list[Commit]:
    """First-parent commits in ``from_ref..to`` (all of ``to``'s history if no ``from_ref``)."""
    to_sha = resolve_commit(to, repo)
    rev_range = to_sha if from_ref is None else f"{resolve_commit(from_ref, repo)}..{to_sha}"
    raw = _git(
        [
            "log",
            "--first-parent",
            "--no-color",
            "--encoding=UTF-8",
            f"--format=%H{_FIELD_SEP}%s{_FIELD_SEP}%B{_RECORD_SEP}",
            rev_range,
        ],
        repo,
    )
    commits: list[Commit] = []
    for record in raw.split(_RECORD_SEP):
        record = record.lstrip("\n")
        if not record:
            continue
        sha, subject, body = record.split(_FIELD_SEP, 2)
        body = normalize_newlines(body)
        commits.append(Commit(sha, subject, body, extract_breaking_footers(body)))
    return commits


def render(commits: Sequence[Commit], from_ref: str | None, to: str) -> str:
    """Render the release body as Markdown."""
    out: list[str] = ["## Breaking changes", ""]
    breaking = [c for c in commits if c.breaking_footers or c.bang]
    if not breaking:
        out += ["None.", ""]
    for commit in breaking:
        out += [f"### {commit.subject} (`{commit.short_sha}`)", ""]
        if not commit.breaking_footers:
            out += [
                "_No `BREAKING CHANGE:` footer: this commit is marked breaking but its "
                "migration steps are missing._",
                "",
            ]
        for footer in commit.breaking_footers:
            out += [footer, ""]
    out += ["## Changes", ""]
    if not commits:
        out += ["No commits in this range.", ""]
    else:
        out += [f"- {c.subject} (`{c.short_sha}`)" for c in commits]
        out.append("")
    scope = f"`{from_ref}..{to}`" if from_ref else f"all history up to `{to}` (no earlier tag)"
    out.append(f"Range: {scope}")
    return "\n".join(out) + "\n"


def ensure_full_history(repo: Path | None = None) -> None:
    """Refuse a shallow clone: its missing history and tags would give a wrong body."""
    if _git(["rev-parse", "--is-shallow-repository"], repo).strip() == "true":
        raise ReleaseNotesError(
            "shallow clone: fetch full history and tags first "
            "(actions/checkout fetch-depth: 0, or git fetch --unshallow --tags)"
        )


def build_notes(to: str = "HEAD", from_ref: str | None = None, repo: Path | None = None) -> str:
    """Resolve the range and return the Markdown release body."""
    ensure_full_history(repo)
    resolve_commit(to, repo)
    start = from_ref if from_ref is not None else previous_tag(to, repo)
    return render(list_commits(start, to, repo), start, to)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print GitHub Release notes (Markdown) for the commits since the "
        "previous vX.Y.Z tag.",
    )
    parser.add_argument("--to", default="HEAD", help="last commit or tag to include (HEAD)")
    parser.add_argument(
        "--from",
        dest="from_ref",
        default=None,
        help="exclusive start ref, honored as given (default: newest exact vX.Y.Z tag "
        "reachable from the first parent of --to)",
    )
    args = parser.parse_args(argv)
    try:
        notes = build_notes(args.to, args.from_ref)
    except ReleaseNotesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
