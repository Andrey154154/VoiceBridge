# VoiceBridge publication guide / Инструкция по публикации

## English

The available GitHub connection supports writing to **existing** repositories but does not expose repository creation or GitHub Release publishing. The publication kit therefore contains **source and release assets**, but **no data has been uploaded** to GitHub by this packaging step.

1. Create a **public** repository named `VoiceBridge` under `Andrey154154` at https://github.com/new?name=VoiceBridge&owner=Andrey154154&visibility=public . Select **Add README** to initialize the `main` branch, but leave the `.gitignore` and license templates unchecked: they are already provided in the prepared files.
2. Upload the contents of `VoiceBridge-GitHub-Source-Apache-2.0.zip` to the repository main branch (replace the initial README.md). Or return to this ChatGPT conversation and ask to publish the prepared source in the newly created repository through GitHub tools.
3. Publish separate GitHub Releases named/tagged `ubuntu-v0.5.6` and `windows-v0.5.4`, attaching their matching ZIPs from `release_assets/` and using the appropriate bilingual release notes from `docs/`.
4. Set the repository description from `REPOSITORY_DESCRIPTION.txt` and optional topics, e.g. `speech-to-text`, `dictation`, `qwen3-asr`, `ubuntu`, `windows`, `python`, `offline-asr`.

Do not upload the original user ZIPs, personal configuration files, previous audit dumps or test logs. Source ZIPs are separate snapshots, not a merged, tested codebase.

## Русский

В подключённом инструменте GitHub есть запись файлов в **существующие** репозитории, но нет создания нового репозитория и загрузки файлов релизов. Поэтому данный комплект **готов к публикации, но сам по себе не опубликован**.

1. Создайте **публичный** репозиторий `VoiceBridge` в `Andrey154154` по ссылке https://github.com/new?name=VoiceBridge&owner=Andrey154154&visibility=public . Включите **Add README** для создания ветки `main`, а шаблоны `.gitignore` и License не выбирайте: они уже подготовлены.
2. Загрузите содержимое `VoiceBridge-GitHub-Source-Apache-2.0.zip` в ветку `main` (заменив автоматически созданный README.md). Можно вместо этого вернуться к ChatGPT и попросить отправить подготовленные исходники через подключённый GitHub.
3. Создайте отдельные GitHub Releases с тегами `ubuntu-v0.5.6` и `windows-v0.5.4`, приложив соответствующие ZIP и двуязычные заметки из `docs/`.
4. Название и описание возьмите из `REPOSITORY_DESCRIPTION.txt`.

**Нельзя отправлять** исходные приватные архивы, персональные настройки, внутренние логи и старые дампы аудита.
