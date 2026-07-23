# syntax=docker/dockerfile:1.7
# claude-org-ja 組織一式 image（設計: docs/design/org-docker-distribution.md）
# ビルドは repo root を context に、この Dockerfile を -f で指定する:
#   docker build -f docker/Dockerfile -t claude-org-ja:local .
# 除外規則は同ディレクトリの Dockerfile.dockerignore（BuildKit per-Dockerfile ignore）。

ARG BASE_IMAGE=debian:bookworm-slim

# ---------------------------------------------------------------------------
# Stage 1: secret-scan — build context に秘匿物が混入していたら build を落とす
# （設計 §7.3。runtime stage はこの stage から COPY するため、scan を通らない
#  コンテンツは image に入らない。禁止パス集合は Dockerfile.dockerignore の
#  除外集合と対で保守すること — ignore が「一層目」、この検査が「二層目」）
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS secret-scan
COPY . /scan
RUN set -eu; \
    fail=0; \
    for p in \
        .git .state .worktrees .venv tmp \
        CLAUDE.local.md .env \
        .claude/settings.local.json \
        .claude/settings.local.override.json \
        .dispatcher/.claude/settings.local.json \
        .dispatcher/.state \
        .curator/.claude/settings.local.json \
        dashboard/data.json \
    ; do \
        if [ -e "/scan/$p" ]; then \
            echo "SECRET-SCAN FAIL: forbidden path in build context: $p" >&2; fail=1; \
        fi; \
    done; \
    if find /scan -maxdepth 2 \( -name '.env.*' -o -name 'settings.local.json*' \) -print -quit | grep -q .; then \
        echo "SECRET-SCAN FAIL: .env.* / settings.local.json* found in build context" >&2; fail=1; \
    fi; \
    if [ -d /scan/knowledge/raw ] && \
       [ -n "$(find /scan/knowledge/raw -type f ! -name '.gitkeep' -print -quit)" ]; then \
        echo "SECRET-SCAN FAIL: knowledge/raw contains files" >&2; fail=1; \
    fi; \
    # tests/ と docs/sandbox-probe/ はダミートークンを意図的に含む（既存の
    # pre-commit secret scanner のフィクスチャ）。パターン grep からのみ除外する。
    # -l: マッチしたファイル名のみを出力する（トークン本体を build ログへ
    # 転写しない — 遮断機構自身を漏洩経路にしないため）
    if grep -rlIE --exclude-dir=tests --exclude-dir=sandbox-probe \
        -e 'sk-ant-[A-Za-z0-9_-]{10,}' \
        -e 'ghp_[A-Za-z0-9]{20,}' \
        -e 'github_pat_[A-Za-z0-9_]{20,}' \
        -e 'xox[bp]-[A-Za-z0-9-]{10,}' \
        -e 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY' \
        /scan >&2; then \
        echo "SECRET-SCAN FAIL: token-shaped pattern found in the files above" >&2; fail=1; \
    fi; \
    [ "$fail" -eq 0 ] || exit 1; \
    echo "secret-scan: clean"

# ---------------------------------------------------------------------------
# Stage 2: org — 実行 image
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS org

ARG ORG_UID=1000
ARG ORG_GID=1000
# runtime pin（設計 §7.7: 起動時 upgrade はせず、更新は image rebuild）。
# 既定値は pyproject.toml の claude-org-runtime floor（下限 pin）と同期させること。
ARG RUNTIME_VERSION=0.1.37
# Claude Code ネイティブインストーラに渡すバージョン（stable | latest | x.y.z）
ARG CLAUDE_CODE_VERSION=stable
ARG INSTALL_HERDR=1
# herdr の pin（sha256 は install-herdr.sh 内に焼き込み。上げるときは両方更新）
ARG HERDR_VERSION=0.7.4
ARG TARGETARCH
# image tag / LABEL に刻む repo ref（設計 §7.7）
ARG REPO_REF=dev

LABEL org.claude-org.repo-ref="${REPO_REF}" \
      org.claude-org.runtime-version="${RUNTIME_VERSION}" \
      org.claude-org.repo-path="/workspace/claude-org-ja" \
      org.claude-org.workers-path="/workspace/workers" \
      org.opencontainers.image.title="claude-org-ja" \
      org.opencontainers.image.description="claude-org-ja org-in-a-box (Claude Code + broker + tmux/herdr + dashboard)"

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git jq tmux sqlite3 socat procps \
        python3 python3-venv \
        tini gosu \
    && rm -rf /var/lib/apt/lists/*

# gh CLI（公式 apt repo、amd64/arm64 両対応）
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# 固定 app user（設計 §8。host bind mount 利用者は --build-arg ORG_UID=$(id -u)）
RUN groupadd -g "${ORG_GID}" org \
    && useradd -m -u "${ORG_UID}" -g "${ORG_GID}" -s /bin/bash org

# claude-org-runtime venv（pure Python、arch 差なし）
RUN python3 -m venv /opt/org-venv \
    && /opt/org-venv/bin/pip install --no-cache-dir \
        "claude-org-runtime==${RUNTIME_VERSION}" \
        "core-harness>=0.3.2,<0.4"

# herdr（設計 §9: 同梱・sha256 検証・self-update 不使用。INSTALL_HERDR=0 で除外可）
COPY docker/install-herdr.sh /usr/local/bin/install-herdr.sh
RUN chmod +x /usr/local/bin/install-herdr.sh \
    && if [ "${INSTALL_HERDR}" = "1" ]; then \
        HERDR_VERSION="${HERDR_VERSION}" /usr/local/bin/install-herdr.sh "${TARGETARCH}"; \
    else \
        echo "herdr: skipped (INSTALL_HERDR=0)"; \
    fi

# repo 本体（scan 済み stage から。設計 §7.3。/workspace/claude-org-ja 固定配置に
# より registry/org-config.md の workers_dir: ../workers が /workspace/workers に
# 自然解決する — 設計 §7.2。.git は含めない: image 内 repo は実行体であり、
# ja 自己編集タスクはコンテナ内では workers への fresh clone で行う — 設計 §7.3）
COPY --from=secret-scan --chown=org:org /scan /workspace/claude-org-ja
RUN mkdir -p /workspace/workers && chown org:org /workspace/workers

# Claude Code CLI（ネイティブインストーラ。設計 §7.7: CLI 実体は volume 化される
# /home/org の外 = /opt/claude-home に置く。HOME 配下に入れると org_home volume に
# 恒久マスクされ、image rebuild で更新できなくなる）
RUN mkdir -p /opt/claude-home \
    && HOME=/opt/claude-home bash -o pipefail -c \
        'curl -fsSL https://claude.ai/install.sh | bash -s -- "$1"' -- "${CLAUDE_CODE_VERSION}" \
    && chmod -R a+rX /opt/claude-home \
    && /opt/claude-home/.local/bin/claude --version

COPY --chmod=755 docker/entrypoint.sh docker/org-shell.sh /usr/local/bin/

# HOME は org ユーザーの home に焼き込む（Issue #735 根治）。base image 既定の
# 空/未設定だと Claude Code・gh・codex 等が認証・設定の保存先を決められず落ちる。
# entrypoint の gosu 降格経路だけでなく docker exec の org-shell --setup 経路
# （子プロセスに entrypoint の export が伝わらない）にも効かせるため image ENV
# に置く。build 中に HOME=/opt/claude-home を要する RUN は各行で inline override
# 済みなので、この runtime 既定は build を汚さない。
ENV HOME=/home/org \
    ORG_TRANSPORT=broker \
    ORG_BACKEND=tmux \
    ORG_BROKER_PORT=48720 \
    ORG_MAX_WORKERS=3 \
    ORG_DASHBOARD_EXPOSE=1 \
    DISABLE_AUTOUPDATER=1 \
    PATH="/opt/org-venv/bin:/opt/claude-home/.local/bin:${PATH}"

WORKDIR /workspace/claude-org-ja

# PID1 は tini（signal forwarding + zombie reap、設計 §5）。
# entrypoint は root で開始し one-time chown 後 gosu org へ降格する（設計 §8）。
# このため image の USER は root のままにする。docker exec の一次導線 org-shell が
# 冒頭で gosu org に自己降格するので、人間の対話面は常に org で動く。
ENTRYPOINT ["tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["daemon"]
