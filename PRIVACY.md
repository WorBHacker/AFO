# Privacy

English · [Русский](#по-русски)

AFO runs on your own computer. It has no account and no server of its own, and no telemetry or analytics: its author
receives nothing from it.

## What stays on your computer

- Chats, projects, settings, memory, logs and the database — in AFO's data folder (`%LOCALAPPDATA%\AFO\data` when
  installed). The uninstaller asks whether to delete them.
- API keys, tokens and passwords — in the Windows Credential Manager. The window only sees whether a key is set; keys
  and tokens are masked in what models see and in the logs.
- The hub listens on this computer only (`127.0.0.1`). Another program (VS Code, an MCP client) needs a token you
  create for it.

## What leaves it — to services you choose, with your own keys and accounts

Each of these services works under its own privacy policy.

- **Cloud models** — [Anthropic](https://www.anthropic.com/legal/privacy),
  [OpenAI](https://openai.com/policies/privacy-policy), [Google Gemini](https://policies.google.com/privacy),
  [DeepSeek](https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html),
  [OpenRouter](https://openrouter.ai/privacy). When you choose one, the conversation, the files you attach and what its
  tools read go to that provider. A project's files go to a cloud model only if you allowed it for that project (off
  by default); mail is read by local models only. With a key set, the hub also asks the provider for its list of
  models.
- **Claude Code** — AFO runs your own `claude` program, which talks to Anthropic under your subscription or key.
- **Web search and web pages** — when a model searches, the query goes to the engine you connected
  ([SearXNG](https://docs.searxng.org) at your address, [Tavily](https://www.tavily.com/privacy) or
  [Brave Search](https://search.brave.com/help/privacy-policy) with your key); when it reads a page, the address is
  fetched.
- **Telegram** ([privacy](https://telegram.org/privacy)) — if you link your own bot: requests for approval and finished
  tasks go to your chat. For a project not allowed to the cloud, the message says only that something waits, without
  code.
- **Mail** — if you connect a mailbox, AFO talks to your mail server (IMAP and SMTP).
- **MCP servers you add** — whatever the models send to them.
- **Hugging Face** ([privacy](https://huggingface.co/privacy)) and **GitHub**
  ([privacy](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement)) — the models,
  engines and tools you download, and «Check the system», which looks whether they can be reached.

## What AFO does by itself

- **Updates** — a minute after the hub starts and then every 12 hours, it asks GitHub for AFO's latest release.
  Nothing about you is sent beyond what any web request carries (your IP address). Turn it off in Settings → About:
  «Check for updates by itself».
- **ripgrep** — the search tool; if it is missing, the hub downloads it from GitHub once.

## Logs for support

«Collect logs for support» makes an archive on your computer, with keys and tokens taken out. It goes nowhere unless
you send it to someone.

## Questions

[GitHub issues](https://github.com/WorBHacker/AFO/issues).

---

## По-русски

AFO работает на вашем компьютере. У программы нет учётной записи и своего сервера, нет телеметрии и аналитики: её
автор не получает от неё ничего.

**Что остаётся на компьютере.** Разговоры, проекты, настройки, память, журналы и база — в папке данных AFO
(`%LOCALAPPDATA%\AFO\data` у установленной программы); при удалении программа спрашивает, удалить ли их. Ключи API,
токены и пароли — в диспетчере учётных данных Windows: окно видит только, задан ли ключ; в том, что видят модели, и в
журналах ключи и токены закрыты. Хаб слушает только этот компьютер (`127.0.0.1`); другой программе (VS Code, клиенту
MCP) нужен токен, который вы для неё создаёте.

**Что уходит — в сервисы, которые выбираете вы, с вашими ключами и учётными записями** (у каждого — своё заявление о
приватности, ссылки — выше):

- облачные модели — Anthropic, OpenAI, Google Gemini, DeepSeek, OpenRouter: когда вы выбираете такую модель, ей
  уходят разговор, приложенные файлы и то, что прочли её инструменты. Файлы проекта — только если вы разрешили облако
  этому проекту (по умолчанию нет); почту читают только локальные модели. С заданным ключом хаб ещё спрашивает у
  поставщика список его моделей;
- Claude Code — AFO запускает вашу программу `claude`, она работает с Anthropic по вашей подписке или ключу;
- поиск и страницы в интернете — запрос уходит в подключённый вами поисковик (SearXNG по вашему адресу, Tavily или
  Brave Search с вашим ключом); страница, которую читает модель, скачивается по её адресу;
- Telegram — если вы привязали своего бота: запросы подтверждения и законченные задачи приходят в ваш чат; у проекта
  без облака — только что что-то ждёт, без кода;
- почта — если подключить ящик, AFO работает с вашим почтовым сервером (IMAP и SMTP);
- серверы MCP, которые вы добавили, — то, что им передают модели;
- Hugging Face и GitHub — модели, движки и инструменты, которые вы скачиваете, и «Проверить систему», которая смотрит,
  доступны ли они.

**Что AFO делает сам.** Обновления: через минуту после старта хаба и затем раз в 12 часов он спрашивает у GitHub
последний выпуск AFO; о вас не уходит ничего сверх того, что несёт любой запрос в интернет (ваш IP-адрес). Выключается в
«Настройки → О программе»: «Проверять обновления сами». ripgrep (инструмент поиска): если его нет, хаб один раз скачивает
его с GitHub.

**Журналы для поддержки.** «Собрать журналы для поддержки» делает архив на вашем компьютере, без ключей и токенов. Он
никуда не уходит, пока вы сами его кому-нибудь не отправите.

**Вопросы** — [GitHub issues](https://github.com/WorBHacker/AFO/issues).
