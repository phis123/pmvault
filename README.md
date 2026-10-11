# pmvault

账号密码管理（Android 版）。本地加密存储：AES-256-GCM + Scrypt/Argon2id，
磁盘上只有密文，主密码不落盘。

- `app/` —— Flet 应用（`main.py` 是入口，`pm_android/` 是界面，`pm_core/` 是加密内核）
- `.github/workflows/build-apk.yml` —— 云端构建签名 APK

构建产物在 Actions 的 Artifacts 里（`pmvault-apk`）。
