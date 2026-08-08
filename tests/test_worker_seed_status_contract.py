"""worker seed の ``Status:`` ヘッダ書式契約の回帰テスト (Refs #835)。

契約 SoT: ``docs/contracts/state-schema-contract.md`` §7
(Set C §1.4 の "FREE-FORM Markdown" から ``Status:`` 行だけを carve out した
additive amendment)。遷移の所有権 (誰がいつ書くか) は Set B §1。

**なぜテストが要るか**: ``claude-org-runtime`` は worker seed の ``Status:``
行を overflow 予約台帳として機械的に読むが (``_seed_status`` /
``count_unbound_reservations``)、読めない書式を**エラーにしない** — ``None``
を返して mtime クロック (``WORKER_BIND_WINDOW_SECONDS = 45``) へ黙って
フォールバックする。症状は「既に active な worker が最長 45 秒 pending 予約と
して枠を占有し、直後の overflow spawn が ``split_capacity_exceeded`` で不当に
拒否される」だけで、45 秒窓に閉じるため手で再現しにくい。書式が壊れたことを
**その場で赤くする**のがこのテストの唯一の役目である。

実 ``.state`` は読まない (``.gitignore`` により CI の checkout には seed が
1 件も無い)。入力は (a) runtime 自身の writer/reader と (b) repo にコミット済み
の prose テンプレートだけで、完全に決定的に回る。
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# **予約台帳が存在するバージョン下限**。``_seed_status`` /
# ``count_unbound_reservations`` / ``WORKER_BIND_WINDOW_SECONDS`` は 0.1.39 で
# 初めて入った (0.1.37 / 0.1.38 の wheel を展開して不在を実測)。ja の依存
# floor は #841 で 0.1.37 → 0.1.39、#868 で 0.1.39 → 0.1.40、#854 で
# 0.1.40 → 0.1.41 と上がっており
# (pyproject.toml / requirements.txt の ``claude-org-runtime`` pin および
# docker/Dockerfile の ``RUNTIME_VERSION``) 現在は
# floor の方が高い。**この下限は pin ではなく runtime の性質**なので pin に
# 追随させず独立に持つ。下限未満の runtime
# では読み手がそもそも居ないので契約に守るべき対象が無く、skip する
# (pin が admit しなくなった今も、古い runtime が入った手元環境で hard fail
# させないための防御として分岐を残す)。
_LEDGER_MIN = (0, 1, 39)
# ja の pin 窓の上限 (< 0.2)。窓の外はこの契約の妥当性が保証外なので skip
# する (tools/check_runtime_schema_drift.py と同じ流儀)。
_PIN_MAX_EXCLUSIVE = (0, 2)


def _installed_runtime_version() -> tuple[int, ...] | None:
    try:
        from importlib.metadata import version

        raw = version("claude-org-runtime")
    except Exception:  # pragma: no cover - メタデータ不在環境
        return None
    parts: list[int] = []
    for chunk in raw.split(".")[:3]:
        digits = re.match(r"\d+", chunk)
        if not digits:
            break
        parts.append(int(digits.group()))
    return tuple(parts) or None


def _skip_reason() -> str | None:
    """契約が適用されない runtime なら skip 理由を返す。

    適用される版 (``_LEDGER_MIN`` 以上・pin 窓の内側) での import 失敗は
    skip ではなく**失敗**にする — reader が動いたこと自体が検出対象であり、
    skip にすると #835 が防ごうとしている silent 化を検出器側で再発させる。
    """
    v = _installed_runtime_version()
    if v is None:
        # バージョンが読めないなら「適用される」と扱って厳格側に倒す。
        return None
    if v < _LEDGER_MIN:
        return (
            f"installed claude-org-runtime {'.'.join(map(str, v))} predates "
            f"the overflow reservation ledger (introduced in "
            f"{'.'.join(map(str, _LEDGER_MIN))}); "
            "§7 の読み手が存在しないため守るべき契約が無い"
        )
    if v[:2] >= _PIN_MAX_EXCLUSIVE:
        return (
            f"installed claude-org-runtime {'.'.join(map(str, v))} is outside "
            "ja's pin window (< 0.2); 契約の妥当性が保証外"
        )
    return None


_CONTRACT_HINT = (
    "docs/contracts/state-schema-contract.md §7 (worker-seed Status: header) "
    "を参照。runtime の reader が動いた場合は契約側を見直すこと。"
)

# prose テンプレートを走査する範囲。docs/ は §7 が禁止書式 (`- Status:`) を
# 引用として載せるため意図的に除外する (契約文書自身を検査対象にしない)。
_PROSE_ROOTS = (".dispatcher", ".claude/skills")

# fenced code block。リスト項目内に字下げされて置かれるので開始/終了とも
# 行頭空白を許す。
_FENCE_RE = re.compile(r"^[ \t]*```[a-zA-Z]*\n(.*?)^[ \t]*```", re.S | re.M)

# worker seed テンプレートの目印。runtime の write_worker_seed
# (runner.py:2998) と同じ 1 行目。
_SEED_MARKER = "# Worker: worker-"


class WorkerSeedStatusContractTest(unittest.TestCase):
    """§7.2 のパース規則と §7.3 の ja 側義務を固定する。"""

    @classmethod
    def setUpClass(cls) -> None:
        skip = _skip_reason()
        if skip is not None:
            raise unittest.SkipTest(f"{skip}; {_CONTRACT_HINT}")
        try:
            from claude_org_runtime.dispatcher.runner import (  # noqa: F401
                WORKER_BIND_WINDOW_SECONDS,
                _seed_status,
                count_unbound_reservations,
                write_worker_seed,
            )
        except (ImportError, AttributeError) as exc:  # pragma: no cover
            raise AssertionError(
                "claude-org-runtime の worker-seed reader を import できない: "
                f"{exc!r}. {_CONTRACT_HINT}"
            ) from exc
        cls.bind_window = WORKER_BIND_WINDOW_SECONDS
        cls.seed_status = staticmethod(_seed_status)
        cls.count_reservations = staticmethod(count_unbound_reservations)
        cls.write_seed = staticmethod(write_worker_seed)

    def _status_of(self, body: str):
        """seed 本文を runtime の実パーサに通した結果を返す。"""
        with tempfile.TemporaryDirectory() as tmp:
            seed = Path(tmp) / "worker-t.md"
            seed.write_text(body, encoding="utf-8")
            return type(self).seed_status(seed)

    # -- §7.2 rule 1-3: パース規則そのもの --------------------------------

    def test_runtime_writer_output_is_readable_by_runtime_reader(self) -> None:
        """runtime の writer/reader が同じ書式で噛み合っていること。

        どちらかが片側だけ動くと (writer がフィールド名を変える / reader が
        パース規則を変える) ここが落ちる。
        """
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / ".state"
            seed = type(self).write_seed(
                state_dir,
                {"task_description": "d"},
                "t1",
                {"cwd": tmp},
            )
            self.assertEqual(type(self).seed_status(seed), "planned")
            self.assertIn(
                "Status: planned",
                seed.read_text(encoding="utf-8"),
                "write_worker_seed が bare `Status: planned` 行を出さなくなった",
            )

    def test_value_is_lowercased_and_whitespace_stripped(self) -> None:
        """§7.2 rule 2: 値は strip + lowercase される。"""
        self.assertEqual(
            self._status_of("# Worker: worker-t\nSTATUS:   Planned  \n"),
            "planned",
        )

    def test_header_wins_over_later_progress_log_mentions(self) -> None:
        """§7.2 rule 1: 最初にマッチした 1 行だけが読まれる。

        Progress Log に "Status: ..." を書く運用が実在する
        (`.state/workers/archive/` に多数) ので、ヘッダ優先が崩れると
        完了済み worker が誤って予約枠を握る / 逆に握らない。
        """
        body = (
            "# Worker: worker-t\n"
            "Status: planned\n"
            "\n"
            "## Progress Log\n"
            "- [10:00] Status: completed と報告\n"
        )
        self.assertEqual(self._status_of(body), "planned")

    def test_unparseable_forms_degrade_to_none(self) -> None:
        """§7.3 の禁止書式が silent degradation を起こすことの negative lock。

        ``None`` は「答えなし」であり、呼び出し側は mtime クロックへ落ちる
        (下の予約テストで実挙動として固定する)。ここが緑のうちは
        「bullet 前置きは読まれない」という契約の前提が生きている。
        """
        self.assertIsNone(
            self._status_of("# Worker: worker-t\n- Status: active\n"),
            "bullet 前置きがパースされるようになった (契約 §7.3 の前提が変化)",
        )
        self.assertIsNone(
            self._status_of("# Worker: worker-t\nTask: t\nStarted: now\n"),
            "Status: 行なしの seed",
        )
        self.assertIsNone(
            self._status_of("# Worker: worker-t\nStatus:   \n"),
            "空値の Status: 行",
        )

    # -- §7.2 rule 4: 予約台帳としての帰結 --------------------------------

    def test_reservation_ledger_treats_missing_status_as_pending(self) -> None:
        """書式を失った seed が「まだ pending」として枠を食うことの実証。

        #835 が防ごうとしている事故そのものを、runtime の公開関数で再現して
        固定する。``planned`` を外した seed は枠を返し、``Status:`` 行を失った
        seed は (active な worker のものであっても) 枠を握り続ける。
        """
        with tempfile.TemporaryDirectory() as tmp:
            workers = Path(tmp) / "workers"
            workers.mkdir(parents=True)
            (workers / "worker-planned.md").write_text(
                "# Worker: worker-planned\nStatus: planned\n", encoding="utf-8"
            )
            (workers / "worker-active.md").write_text(
                "# Worker: worker-active\nStatus: active\n", encoding="utf-8"
            )
            (workers / "worker-headerless.md").write_text(
                "# Worker: worker-headerless\nTask: t\nStarted: now\n",
                encoding="utf-8",
            )
            fresh = max(p.stat().st_mtime for p in workers.glob("*.md"))

            reserved = type(self).count_reservations(
                Path(tmp), (), now=fresh + 1
            )
            self.assertEqual(
                reserved,
                ("worker-headerless", "worker-planned"),
                "Status: 行を失った seed が予約枠を握る挙動 (§7.5) が変化した",
            )

            # 窓を過ぎれば mtime で失効する = degradation は 45 秒で自然治癒
            # するが、その 45 秒が不当な split_capacity_exceeded を生む。
            expired = type(self).count_reservations(
                Path(tmp), (), now=fresh + type(self).bind_window + 1
            )
            self.assertEqual(expired, ())

    def test_ledger_glob_is_non_recursive_and_matches_dotfiles(self) -> None:
        """§7.3 「workers/ 直下は seed 専用」の根拠を固定する。

        archive/ は走査対象外 (だから旧書式の移行が不要)、一方で dotfile は
        走査対象に入る (だから運用メモを直下に置くと phantom 予約になる)。
        """
        with tempfile.TemporaryDirectory() as tmp:
            workers = Path(tmp) / "workers"
            (workers / "archive").mkdir(parents=True)
            (workers / "archive" / "worker-old.md").write_text(
                "# Worker: worker-old\nStatus: planned\n", encoding="utf-8"
            )
            (workers / ".notes.md").write_text("memo\n", encoding="utf-8")
            fresh = max(
                p.stat().st_mtime for p in workers.rglob("*.md")
            )

            reserved = type(self).count_reservations(
                Path(tmp), (), now=fresh + 1
            )
            self.assertEqual(reserved, (".notes",))

    # -- §7.3: ja 側 prose テンプレートの義務 ------------------------------

    def test_prose_worker_seed_templates_carry_parseable_status(self) -> None:
        """ja の prose に埋まった seed テンプレートを実パーサに通す。

        dispatcher は helper 未経由のフォールバック経路でこのテンプレートを
        手で書き起こすので、テンプレートから ``Status:`` 行が落ちれば実 seed
        から落ちる。#835 で実際に見つかった不具合
        (`.dispatcher/references/spawn-flow.md` Step 4) を固定する。
        """
        found: list[tuple[str, str]] = []
        for root in _PROSE_ROOTS:
            for md in sorted((REPO_ROOT / root).rglob("*.md")):
                text = md.read_text(encoding="utf-8", errors="replace")
                for block in _FENCE_RE.findall(text):
                    if _SEED_MARKER in block:
                        found.append(
                            (str(md.relative_to(REPO_ROOT)), block)
                        )

        self.assertTrue(
            found,
            "worker seed テンプレートを含む fenced block が 1 つも無い。"
            "テンプレートが移動したならこのテストの走査範囲を更新すること "
            f"({', '.join(_PROSE_ROOTS)})",
        )
        for where, block in found:
            with self.subTest(template=where):
                status = self._status_of(block)
                self.assertIsNotNone(
                    status,
                    f"{where} の worker seed テンプレートに runtime が読める "
                    f"`Status:` 行が無い。{_CONTRACT_HINT}",
                )
                # ja の prose に載る seed テンプレートは今日すべて **spawn 後**
                # に手で書き起こすものなので、`planned` を残すと「まだ bind
                # 待ち」を意味してしまい、行が無いのと同じ枠占有バグになる
                # (§7.3 の flip 義務)。将来 pre-spawn テンプレートを prose に
                # 置くなら、ここで落ちるのが正しい合図。
                self.assertNotEqual(
                    status,
                    "planned",
                    f"{where} の post-spawn テンプレートが `planned` のまま。"
                    f"予約枠を握り続ける (§7.2 rule 4)。{_CONTRACT_HINT}",
                )

        # 実バグが出た当の 1 件は、走査ロジックの綻びで取りこぼされないよう
        # 名指しでも固定する。
        by_file = {where: block for where, block in found}
        spawn_flow = ".dispatcher/references/spawn-flow.md"
        self.assertIn(spawn_flow, by_file, "Step 4 のテンプレートが消えた")
        self.assertEqual(
            self._status_of(by_file[spawn_flow]),
            "active",
            f"{spawn_flow} Step 4 (MCP spawn 成功後) のテンプレートは "
            f"`Status: active` であること。{_CONTRACT_HINT}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
