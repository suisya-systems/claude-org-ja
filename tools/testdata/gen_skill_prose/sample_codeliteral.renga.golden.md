# sample-codeliteral

Step 0: transport 判定。

```bash
echo "${ORG_TRANSPORT:-broker}"
```

既定値は `broker`（render 面トークン `renga-peers` とは別系統）。
