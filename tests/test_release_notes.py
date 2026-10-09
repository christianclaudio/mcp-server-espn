"""Tests for scripts/release_notes.py against throwaway git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import release_notes
from scripts.release_notes import (
    Commit,
    ReleaseNotesError,
    build_notes,
    extract_breaking_footers,
    list_commits,
    previous_tag,
    render,
    resolve_commit,
)


class Repo:
    """A temporary git repository with deterministic identity and dates."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.count = 0
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, stdin: str | None = None) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=self.path,
            input=stdin,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def commit(self, message: str) -> str:
        self.count += 1
        date = f"2026-01-01T00:00:{self.count:02d}+00:00"
        self.git(
            "-c",
            f"user.name=Test {self.count}",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "--allow-empty",
            "--cleanup=verbatim",
            f"--date={date}",
            "-F",
            "-",
            stdin=message,
        )
        return self.git("rev-parse", "HEAD").strip()

    def tag(self, name: str, *, annotated: bool = False) -> None:
        if annotated:
            self.git(
                "-c", "user.name=T", "-c", "user.email=t@example.com", "tag", "-a", name, "-m", name
            )
        else:
            self.git("tag", name)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    path = tmp_path / "repo"
    path.mkdir()
    monkeypatch.chdir(path)
    return Repo(path)


MULTILINE = """feat!: rename the readonly profile (#50)

Renames the profile and drops the old alias.

BREAKING CHANGE: the `readonly` profile is now `read`.

Migration:
1. Replace `--profile readonly` with `--profile read`.
2. Read-only: set `TEMPLATE_MCP_PROFILE=read` in client configs.

```bash
template-mcp --profile read
```
Co-authored-by: Someone <someone@example.com>
Signed-off-by: Christian Claudio <c@example.com>
"""

MULTILINE_FOOTER = """BREAKING CHANGE: the `readonly` profile is now `read`.

Migration:
1. Replace `--profile readonly` with `--profile read`.
2. Read-only: set `TEMPLATE_MCP_PROFILE=read` in client configs.

```bash
template-mcp --profile read
```"""


# --- footer extraction (pure) -------------------------------------------------------------


def test_multiline_footer_is_verbatim_up_to_trailers() -> None:
    assert extract_breaking_footers(MULTILINE) == (MULTILINE_FOOTER,)


def test_footer_runs_to_end_of_message_and_drops_trailing_blank_lines() -> None:
    msg = "fix!: x\n\nBREAKING CHANGE: a\n  indented continuation\n\nsecond paragraph\n\n\n"
    assert extract_breaking_footers(msg) == (
        "BREAKING CHANGE: a\n  indented continuation\n\nsecond paragraph",
    )


def test_synonym_and_multiple_footers_split_at_next_token() -> None:
    msg = "feat!: x\n\nBREAKING CHANGE: first\nmore\nBREAKING-CHANGE: second\nRefs #12\nafter"
    assert extract_breaking_footers(msg) == (
        "BREAKING CHANGE: first\nmore",
        "BREAKING-CHANGE: second",
    )


def test_token_with_text_on_following_lines() -> None:
    msg = "feat!: x\n\nBREAKING CHANGE:\n- tool `a` removed\n- use `b`"
    assert extract_breaking_footers(msg) == ("BREAKING CHANGE:\n- tool `a` removed\n- use `b`",)


def test_crlf_is_normalized() -> None:
    msg = "feat!: x\r\n\r\nBREAKING CHANGE: a\r\nMigration: b\r\n"
    assert extract_breaking_footers(msg) == ("BREAKING CHANGE: a\nMigration: b",)


@pytest.mark.parametrize(
    "msg",
    [
        "BREAKING CHANGE: in the subject only",
        "docs: x\n\nThis is a BREAKING CHANGE: mid-line prose.",
        "docs: x\n\nbreaking change: lowercase is not a token",
        "docs: x\n\nBREAKING CHANGES: plural is not a token",
        "docs: x\n\n```\nBREAKING CHANGE: quoted in a fence\n```\n",
        "docs: x\n\n~~~\nBREAKING CHANGE: quoted in a tilde fence\n~~~\n",
    ],
)
def test_non_footers_are_ignored(msg: str) -> None:
    assert extract_breaking_footers(msg) == ()


def test_nested_longer_outer_fence_keeps_breaking_quoted() -> None:
    """Four-backtick outer wrapping a three-backtick block must not flip early."""
    msg = "docs: x\n\n````outer\n```\nBREAKING CHANGE: quoted inside nested fences\n```\n````\n"
    assert extract_breaking_footers(msg) == ()


def test_mismatched_fence_character_does_not_close() -> None:
    """A tilde close cannot end a backtick fence (and vice versa)."""
    msg = (
        "docs: x\n\n"
        "```\n"
        "BREAKING CHANGE: still inside backtick fence\n"
        "~~~\n"
        "still fenced\n"
        "```\n"
        "\n"
        "BREAKING CHANGE: the real footer.\n"
    )
    assert extract_breaking_footers(msg) == ("BREAKING CHANGE: the real footer.",)


def test_shorter_closing_fence_does_not_close() -> None:
    """Closing run must be at least as long as the opening run."""
    msg = (
        "docs: x\n\n"
        "`````\n"
        "BREAKING CHANGE: still inside five-backtick fence\n"
        "```\n"
        "still fenced\n"
        "`````\n"
    )
    assert extract_breaking_footers(msg) == ()


# CommonMark 0.31.2, section 4.5 (Fenced code blocks). Each case is modeled on the
# spec example in its id, written for this parser: a "BREAKING CHANGE: quoted" line
# must stay inside the fence (not extracted) and "BREAKING CHANGE: real" must be
# outside it (extracted). The message subject "docs: x" is never parsed.
QUOTED = "BREAKING CHANGE: quoted"
REAL = "BREAKING CHANGE: real"
COMMONMARK_FENCES = [
    pytest.param(f"```\n{QUOTED}\n```\n\n{REAL}", (REAL,), id="ex119-backtick-fence"),
    pytest.param(f"~~~\n{QUOTED}\n~~~\n\n{REAL}", (REAL,), id="ex120-tilde-fence"),
    pytest.param(f"``\n{REAL}\n``", (f"{REAL}\n``",), id="ex121-two-backticks-not-a-fence"),
    pytest.param(
        f"```\naaa\n~~~\n{QUOTED}\n```\n\n{REAL}", (REAL,), id="ex122-tilde-does-not-close-backtick"
    ),
    pytest.param(
        f"~~~\naaa\n```\n{QUOTED}\n~~~\n\n{REAL}", (REAL,), id="ex123-backtick-does-not-close-tilde"
    ),
    pytest.param(
        f"````\naaa\n```\n{QUOTED}\n``````\n\n{REAL}",
        (REAL,),
        id="ex124-shorter-no-close-longer-closes",
    ),
    pytest.param(
        f"~~~~\naaa\n~~~\n{QUOTED}\n~~~~\n\n{REAL}",
        (REAL,),
        id="ex125-shorter-tilde-does-not-close",
    ),
    pytest.param(f"```\n{QUOTED}", (), id="ex126-unclosed-runs-to-end"),
    pytest.param(f"`````\n\n```\n{QUOTED}\naaa\n", (), id="ex127-unclosed-with-shorter-inner"),
    pytest.param(f"```\n\n  \n{QUOTED}\n```\n\n{REAL}", (REAL,), id="ex129-blank-content-lines"),
    pytest.param(f"```\n```\n{REAL}", (REAL,), id="ex130-empty-fence"),
    pytest.param(f" ```\n{QUOTED}\n```\n\n{REAL}", (REAL,), id="ex131-opener-indent-1"),
    pytest.param(f"  ```\n{QUOTED}\n  ```\n\n{REAL}", (REAL,), id="ex132-opener-indent-2"),
    pytest.param(f"   ```\n{QUOTED}\n   ```\n\n{REAL}", (REAL,), id="ex133-opener-indent-3"),
    pytest.param(
        f"    ```\n{REAL}\n    ```", (f"{REAL}\n    ```",), id="ex134-opener-indent-4-not-a-fence"
    ),
    pytest.param(f"```\n{QUOTED}\n  ```\n\n{REAL}", (REAL,), id="ex135-closer-indent-2"),
    pytest.param(f"   ```\n{QUOTED}\n  ```\n\n{REAL}", (REAL,), id="ex136-closer-indent-differs"),
    pytest.param(f"```\naaa\n    ```\n{QUOTED}", (), id="ex137-closer-indent-4-does-not-close"),
    pytest.param(f"``` ```\n{REAL}", (REAL,), id="ex138-backtick-info-with-backtick"),
    pytest.param(f"~~~~~~\naaa\n~~~ ~~\n{QUOTED}", (), id="ex139-closer-with-text-does-not-close"),
    pytest.param(
        f"foo\n```\n{QUOTED}\n```\nbaz\n\n{REAL}", (REAL,), id="ex140-fence-interrupts-paragraph"
    ),
    pytest.param(
        f"foo\n---\n~~~\n{QUOTED}\n~~~\n\n{REAL}", (REAL,), id="ex141-after-setext-heading"
    ),
    pytest.param(f"```ruby\n{QUOTED}\n```\n\n{REAL}", (REAL,), id="ex142-info-string"),
    pytest.param(
        f"~~~~    ruby startline=3 $%@#$\n{QUOTED}\n~~~~~~~\n\n{REAL}",
        (REAL,),
        id="ex143-tilde-info-string-longer-closer",
    ),
    pytest.param(f"````;\n````\n{REAL}", (REAL,), id="ex144-punctuation-info-string"),
    pytest.param(f"``` aa ```\n{REAL}", (REAL,), id="ex145-backtick-info-with-backticks"),
    pytest.param(f"```code```\n\n{REAL}", (REAL,), id="ex145-inline-code-line-before-footer"),
    pytest.param(
        f"~~~ aa ``` ~~~\n{QUOTED}\n~~~\n\n{REAL}", (REAL,), id="ex146-tilde-info-with-backticks"
    ),
    pytest.param(
        f"```\n``` aaa\n{QUOTED}\n```\n\n{REAL}",
        (REAL,),
        id="ex147-closer-with-info-does-not-close",
    ),
    pytest.param(
        f"```\n{QUOTED}\n``` \t \n\n{REAL}", (REAL,), id="closer-trailing-spaces-and-tabs"
    ),
    pytest.param(f"\t```\n{REAL}", (REAL,), id="tab-indent-not-a-fence"),
]


@pytest.mark.parametrize(("body", "expected"), COMMONMARK_FENCES)
def test_commonmark_fenced_code_blocks(body: str, expected: tuple[str, ...]) -> None:
    assert extract_breaking_footers("docs: x\n\n" + body) == expected


def test_block_quote_fence_is_not_modeled() -> None:
    """Pinned limitation (CommonMark example 128): a fence in a block quote is not modeled.

    The quoted lines start with ``>``, so they are never tokens either way, and the real
    footer after the block quote is still found.
    """
    msg = f"docs: x\n\n> ```\n> {QUOTED}\n\n{REAL}"
    assert extract_breaking_footers(msg) == (REAL,)


def test_list_item_fence_counts_as_top_level() -> None:
    """Pinned limitation: list items are not modeled.

    A fence after a ``- `` list marker is not an opening fence here, and the list
    item's indented closing fence opens a top-level fence instead, so a footer after
    it is hidden. CommonMark would close the fence inside the list item and find the
    footer.
    """
    msg = f"docs: x\n\n- ```\n  ```\n{REAL}"
    assert extract_breaking_footers(msg) == ()


def test_indented_token_is_not_a_footer() -> None:
    assert extract_breaking_footers(f"docs: x\n\n {REAL}") == ()
    assert extract_breaking_footers(f"docs: x\n\n    {REAL}") == ()


def test_trailer_keys_are_case_insensitive() -> None:
    msg = "feat!: x\n\nBREAKING CHANGE: a\nco-authored-by: B <b@example.com>\ntail"
    assert extract_breaking_footers(msg) == ("BREAKING CHANGE: a",)


# --- rendering (pure) ---------------------------------------------------------------------


def test_render_empty_range() -> None:
    assert render([], "v1.0.0", "v1.0.0") == (
        "## Breaking changes\n\nNone.\n\n## Changes\n\nNo commits in this range.\n\n"
        "Range: `v1.0.0..v1.0.0`\n"
    )


def test_render_bang_without_footer_is_flagged() -> None:
    commit = Commit("a" * 40, "feat(api)!: drop v1", "feat(api)!: drop v1\n")
    out = render([commit], None, "HEAD")
    assert "### feat(api)!: drop v1 (`aaaaaaa`)" in out
    assert "migration steps are missing" in out
    assert out.endswith("Range: all history up to `HEAD` (no earlier tag)\n")


# --- git integration ----------------------------------------------------------------------


def test_no_previous_tag_covers_all_history(repo: Repo) -> None:
    first = repo.commit("chore: init")
    second = repo.commit("feat: add tool")
    assert previous_tag("HEAD") is None
    assert build_notes() == (
        "## Breaking changes\n\nNone.\n\n## Changes\n\n"
        f"- feat: add tool (`{second[:7]}`)\n- chore: init (`{first[:7]}`)\n\n"
        "Range: all history up to `HEAD` (no earlier tag)\n"
    )


def test_previous_tag_is_found_from_head(repo: Repo) -> None:
    repo.commit("chore: init")
    repo.tag("v1.0.0")
    fix = repo.commit("fix: handle nulls")
    breaking = repo.commit(MULTILINE)
    assert previous_tag("HEAD") == "v1.0.0"
    assert build_notes() == (
        "## Breaking changes\n\n"
        f"### feat!: rename the readonly profile (#50) (`{breaking[:7]}`)\n\n"
        f"{MULTILINE_FOOTER}\n\n"
        "## Changes\n\n"
        f"- feat!: rename the readonly profile (#50) (`{breaking[:7]}`)\n"
        f"- fix: handle nulls (`{fix[:7]}`)\n\n"
        "Range: `v1.0.0..HEAD`\n"
    )


def test_to_tag_skips_itself_and_later_commits(repo: Repo) -> None:
    repo.commit("chore: init")
    repo.tag("v1.0.0", annotated=True)
    repo.commit("feat: one")
    repo.tag("v1.1.0", annotated=True)
    repo.commit("feat: after the release")
    notes = build_notes(to="v1.1.0")
    assert "- feat: one (" in notes
    assert "after the release" not in notes
    assert "chore: init" not in notes
    assert notes.endswith("Range: `v1.0.0..v1.1.0`\n")


def test_head_on_a_tag_uses_the_tag_before_it(repo: Repo) -> None:
    repo.commit("chore: init")
    repo.tag("v1.0.0")
    repo.commit("fix: one")
    repo.tag("v1.0.1")
    assert previous_tag("HEAD") == "v1.0.0"


def test_explicit_from_and_non_version_tags(repo: Repo) -> None:
    repo.commit("chore: init")
    repo.tag("v1.0.0")
    repo.commit("feat: a")
    repo.tag("release-candidate")
    repo.tag("vnext")
    repo.commit("feat: b")
    assert previous_tag("HEAD") == "v1.0.0"
    notes = build_notes(from_ref="release-candidate")
    assert "feat: b" in notes
    assert "feat: a" not in notes


def test_previous_tag_skips_non_exact_semver_tags(repo: Repo) -> None:
    repo.commit("chore: init")
    repo.tag("v1.2.2")
    fix_a = repo.commit("fix: a")
    repo.tag("v1.2.3.post1")
    fix_b = repo.commit("fix: b")
    repo.tag("v1.2.4-rc1")
    repo.commit("fix: c")
    assert previous_tag("HEAD") == "v1.2.2"
    notes = build_notes()
    assert f"- fix: a (`{fix_a[:7]}`)" in notes
    assert f"- fix: b (`{fix_b[:7]}`)" in notes
    assert "chore: init" not in notes
    assert notes.endswith("Range: `v1.2.2..HEAD`\n")


def test_exact_tag_wins_over_a_suffixed_tag_on_the_same_commit(repo: Repo) -> None:
    repo.commit("chore: init")
    repo.tag("v1.2.3")
    repo.tag("v1.2.3.post1")
    repo.commit("fix: a")
    assert previous_tag("HEAD") == "v1.2.3"


def test_only_non_exact_tags_means_no_previous_tag(repo: Repo) -> None:
    repo.commit("chore: init")
    repo.tag("v1.2.3.post1")
    repo.commit("fix: a")
    assert previous_tag("HEAD") is None


def test_explicit_from_non_exact_tag_is_honored(repo: Repo) -> None:
    """``--from`` is the maintainer's choice, so a non-exact tag is used as given."""
    repo.commit("chore: init")
    repo.tag("v1.2.2")
    repo.commit("fix: a")
    repo.tag("v1.2.3.post1")
    fix_b = repo.commit("fix: b")
    notes = build_notes(from_ref="v1.2.3.post1")
    assert f"- fix: b (`{fix_b[:7]}`)" in notes
    assert "fix: a" not in notes
    assert notes.endswith("Range: `v1.2.3.post1..HEAD`\n")


def test_root_commit_as_to(repo: Repo) -> None:
    root = repo.commit("chore: init")
    repo.commit("feat: later")
    assert previous_tag(root) is None
    assert f"- chore: init (`{root[:7]}`)" in build_notes(to=root)


def test_first_parent_only(repo: Repo) -> None:
    repo.commit("chore: init")
    repo.tag("v1.0.0")
    repo.git("checkout", "-q", "-b", "topic")
    repo.commit("wip: side commit")
    repo.git("checkout", "-q", "main")
    repo.git(
        "-c",
        "user.name=T",
        "-c",
        "user.email=t@example.com",
        "merge",
        "-q",
        "--no-ff",
        "topic",
        "-m",
        "feat: merged topic",
    )
    subjects = [c.subject for c in list_commits("v1.0.0", "HEAD")]
    assert subjects == ["feat: merged topic"]


def test_output_is_deterministic(repo: Repo) -> None:
    repo.commit("chore: init")
    repo.tag("v1.0.0")
    repo.commit(MULTILINE.replace("\n", "\r\n"))
    first = build_notes(to="HEAD")
    assert first == build_notes(to="HEAD")
    assert "\r" not in first
    assert MULTILINE_FOOTER in first


def test_unknown_refs_raise(repo: Repo) -> None:
    repo.commit("chore: init")
    with pytest.raises(ReleaseNotesError, match="unknown revision: v9.9.9"):
        resolve_commit("v9.9.9")
    with pytest.raises(ReleaseNotesError, match="unknown revision: nope"):
        build_notes(from_ref="nope")


def test_shallow_clone_is_refused(repo: Repo, tmp_path: Path) -> None:
    repo.commit("chore: init")
    repo.tag("v1.0.0")
    repo.commit("feat: a")
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", repo.path.as_uri(), str(shallow)],
        check=True,
        capture_output=True,
    )
    with pytest.raises(ReleaseNotesError, match="shallow clone"):
        build_notes(repo=shallow)


def test_git_failure_message(tmp_path: Path) -> None:
    with pytest.raises(ReleaseNotesError, match="git log failed"):
        release_notes._git(["log"], tmp_path)


def test_main_prints_to_stdout(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.commit("chore: init")
    repo.tag("v1.0.0")
    repo.commit("feat: a")
    repo.tag("v1.1.0")
    assert release_notes.main(["--to", "v1.1.0"]) == 0
    captured = capsys.readouterr()
    assert captured.out == build_notes(to="v1.1.0")
    assert captured.err == ""


def test_main_reports_errors(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.commit("chore: init")
    assert release_notes.main(["--from", "v0.0.0-missing"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: unknown revision: v0.0.0-missing\n"


def test_line_start_token_mid_body_is_not_a_footer() -> None:
    msg = "docs: x\n\n## Notes\n\nBREAKING CHANGE: none here.\n\n## Test plan\n\n- ran tests\n"
    assert extract_breaking_footers(msg) == ()


def test_heading_before_final_footer_is_fine() -> None:
    msg = "feat!: x\n\n## Summary\n\ntext\n\nBREAKING CHANGE: a\n\nMigration: b\n"
    assert extract_breaking_footers(msg) == ("BREAKING CHANGE: a\n\nMigration: b",)


CODERABBIT_BLOCK = (
    "<!-- This is an auto-generated comment: release notes by coderabbit.ai -->\n"
    "## Summary by CodeRabbit\n"
    "\n"
    "* **New Features**\n"
    "  * Renamed the readonly profile.\n"
)
CODERABBIT_END = "\n<!-- end of auto-generated comment: release notes by coderabbit.ai -->\n"


@pytest.mark.parametrize("closing", ["", CODERABBIT_END], ids=["unclosed", "closed"])
def test_coderabbit_block_after_footer_is_ignored(closing: str) -> None:
    msg = (
        "feat!: rename the readonly profile\n\n## Summary\n\ntext\n\n"
        "BREAKING CHANGE: the `readonly` profile is now `read`.\n\n"
        "To migrate, replace `--profile readonly` with `--profile read`.\n\n"
        + CODERABBIT_BLOCK
        + closing
    )
    assert extract_breaking_footers(msg) == (
        "BREAKING CHANGE: the `readonly` profile is now `read`.\n\n"
        "To migrate, replace `--profile readonly` with `--profile read`.",
    )


def test_coderabbit_marker_without_footer_has_no_breaking_entry() -> None:
    # A token inside the generated block would be a footer if the marker were not a hard end.
    msg = "fix: x\n\nbody\n\n" + CODERABBIT_BLOCK + "\nBREAKING CHANGE: generated text\n"
    assert extract_breaking_footers(msg) == ()
    commit = Commit("a" * 40, "fix: x", msg, extract_breaking_footers(msg))
    assert render([commit], "v1.0.0", "HEAD").startswith("## Breaking changes\n\nNone.\n")


def test_coderabbit_marker_ends_the_squash_message(repo: Repo) -> None:
    repo.commit("chore: init\n")
    repo.tag("v1.0.0")
    repo.commit(
        "feat!: drop x\n\nBREAKING CHANGE: x is gone.\n\nMigration: use y.\n\n"
        + CODERABBIT_BLOCK
        + CODERABBIT_END
    )
    notes = build_notes()
    assert "BREAKING CHANGE: x is gone.\n\nMigration: use y.\n\n## Changes" in notes
    assert "CodeRabbit" not in notes
    assert "coderabbit.ai" not in notes


def test_coderabbit_marker_inside_a_fence_does_not_end_the_message() -> None:
    msg = (
        "feat!: document the marker\n\n## Summary\n\nCodeRabbit appends this line:\n\n"
        "```text\n" + CODERABBIT_BLOCK + "BREAKING CHANGE: quoted, not real\n```\n\n"
        "BREAKING CHANGE: the real footer.\n\nMigration: do y.\n"
    )
    assert extract_breaking_footers(msg) == (
        "BREAKING CHANGE: the real footer.\n\nMigration: do y.",
    )
