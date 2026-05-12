# Daily Finance News Digest

每天自动收集财经、AI、黄金相关新闻，用 AI 生成中文摘要和分析，然后发送到 Gmail。

默认主题：

- 重点来源：华尔街见闻
- 重点来源：虎嗅
- 中国科创50
- 纳斯达克指数
- 中证500
- 沪深300
- 人工智能
- 黄金

默认发送时间：`America/Los_Angeles` 旧金山时间上午 `11` 点。

## 运行方式

GitHub Actions 会在 GitHub 云端运行，所以不需要你的电脑开机，也不需要自己租服务器。

workflow 每小时触发一次，但脚本会检查当前旧金山时间，只有到配置的小时才会发送邮件。这样可以自动处理美国夏令时和冬令时。

## Gmail 授权码

不要使用 Gmail 登录密码。需要创建 Gmail 应用专用密码：

1. 打开 <https://myaccount.google.com/security>
2. 开启“两步验证”
3. 回到安全页面，搜索 `App passwords` 或“应用专用密码”
4. 创建一个新密码，名称可以写 `GitHub Actions News Digest`
5. Google 会生成一串 16 位左右的密码
6. 把它保存到 GitHub Secrets，变量名为 `GMAIL_APP_PASSWORD`

如果页面里找不到“应用专用密码”，通常是因为两步验证还没开启，或账号策略不允许创建。

## GitHub Secrets

进入 GitHub 仓库：

`Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`

添加这些 Secrets：

- `GMAIL_USER`: `yokopenn235@gmail.com`
- `GMAIL_APP_PASSWORD`: Gmail 应用专用密码
- `TO_EMAIL`: `yokopenn235@gmail.com`
- `OPENAI_API_KEY`: 你的 OpenAI API Key

## 可调配置

进入 GitHub 仓库：

`Settings` -> `Secrets and variables` -> `Actions` -> `Variables`

可选添加这些 Variables：

- `DIGEST_TIMEZONE`: 默认 `America/Los_Angeles`
- `DIGEST_HOUR`: 默认 `11`
- `OPENAI_MODEL`: 默认 `gpt-4o-mini`
- `ARTICLES_PER_TOPIC`: 默认 `5`
- `PRIORITY_ARTICLES_PER_SOURCE`: 默认 `6`

例如，如果想改成纽约时间上午 9 点：

- `DIGEST_TIMEZONE`: `America/New_York`
- `DIGEST_HOUR`: `9`

## 手动测试

在 GitHub 仓库页面打开：

`Actions` -> `Daily Finance News Digest` -> `Run workflow`

默认会立即发送一封测试邮件。

## 本地测试

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py --send-now
```

本地运行前，需要把 `.env` 里的密钥替换成真实值。不要把 `.env` 提交到 GitHub。

## 推送到 GitHub

如果本地还没有设置远程仓库：

```bash
git remote add origin https://github.com/yoyoyokoko23/news.git
git branch -M main
git add .
git commit -m "Add daily finance news digest"
git push -u origin main
```
