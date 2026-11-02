# Website on Namecheap

`site/` is the website. Anything pushed to `main` under `site/` is uploaded to the Namecheap hosting automatically by **Deploy to Namecheap** (`.github/workflows/deploy.yml`).

## One-time setup

1. In Namecheap cPanel → **FTP Accounts**, create an FTP account whose directory is `public_html`. The workflows upload to the folder the account opens in.
2. In this repository → **Settings → Secrets and variables → Actions → New repository secret**, add:

   | Secret | Example |
   |---|---|
   | `FTP_SERVER` | the server name from cPanel → *General Information*, e.g. `server123.web-hosting.com` (its security certificate matches, so use it rather than `ftp.yourdomain.lv`) |
   | `FTP_USERNAME` | `deploy@yourdomain.lv` |
   | `FTP_PASSWORD` | the FTP account password |

3. **Actions → Import current site from Namecheap → Run workflow.** This copies the live site into `site/`.

From then on, every change to `site/` goes live in about a minute. The Actions tab shows each upload, and any earlier version can be restored from the commit history.

## Notes

- Only changed files are uploaded. Files on the server that did not come from this repository are never deleted.
- Passwords live only in GitHub Secrets; they are never written into the repository.
