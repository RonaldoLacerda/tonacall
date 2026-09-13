# ToNaCall — transcrição inteligente de reuniões

Fluxo: usuário cola o link → seu backend manda o Attendee.dev entrar na
reunião → quando termina, baixa a gravação e transcreve local com
faster-whisper (grátis, sem pagar API de transcrição).

## 1. Pegue uma API key gratuita do Attendee

1. Crie uma conta grátis em https://app.attendee.dev
2. Crie um projeto e gere uma API key (seção "API Keys" no menu lateral)
3. Copie `.env.example` para `.env` e cole a key:
   ```
   ATTENDEE_API_KEY=sua_key_aqui
   ```

Isso usa a instância **hospedada** do Attendee — não precisa de Docker
nem servidor próprio pra testar. Docker só entra em cena se depois você
quiser fazer self-host (ver seção final).

## 2. Instale as dependências

Precisa também do **ffmpeg** instalado no sistema (extrai o áudio do mp4
da gravação):

- Windows: baixe em https://www.gyan.dev/ffmpeg/builds/ e adicione a
  pasta `bin` ao PATH (ou `choco install ffmpeg` se tiver Chocolatey)
- Depois:
  ```bash
  python -m venv venv
  venv\Scripts\activate          # Windows
  # source venv/bin/activate     # Linux/Mac
  pip install -r requirements.txt
  ```

O projeto usa o modelo `small` do Whisper por padrão para melhorar a
precisão em português. Na primeira execução, ele será baixado. Em um
computador mais lento, você pode priorizar velocidade no `.env`:
```
WHISPER_MODEL=tiny
WHISPER_CPU_THREADS=4
```

## 3. Rode

```bash
python -m uvicorn main:app --reload --port 8080
```

Abra http://localhost:8080 — cola o link do Meet, clica em "Entrar e
transcrever". O bot do Attendee entra na call sozinho (ele pede admissão
igual um convidado normal), e quando a reunião terminar, a página mostra
a transcrição e libera o player do áudio automaticamente (fica checando o
status a cada 5s). O áudio convertido fica salvo localmente em `recordings/`.

## Como funciona por baixo dos panos

1. `POST /meetings` → chama `POST /api/v1/bots` do Attendee com o link
2. Uma thread em background fica checando `GET /api/v1/bots/{id}` a cada
   15s até o estado virar `ended`
3. Quando termina, `GET /api/v1/bots/{id}/recording` retorna uma URL
   temporária do mp4 da gravação
4. O backend baixa o mp4, normaliza e salva o áudio WAV em `recordings/`,
   transcreve em português com `faster-whisper` (modelo `small`, local, gratuito) e
  guarda o texto em memória
5. O frontend, que fica dando polling, mostra o resultado assim que
   aparece

## Próximos passos pra virar produto de verdade

- **Trocar polling por webhook**: em vez de ficar checando o status a
  cada 15s, configure um webhook (`bot.state_change`) no Attendee
  apontando pra um endpoint seu — mais eficiente e sem atraso. Precisa
  de uma URL pública (ex: `ngrok` em dev, domínio real em produção).
- **Banco de verdade**: hoje `MEETINGS` é um dicionário em memória — some
  se reiniciar o servidor. Trocar por Postgres/SQLite quando for sério.
- **Fila de jobs**: se muitos usuários usarem ao mesmo tempo, mover o
  processamento (download + transcrição) pra uma fila (Celery/RQ) em vez
  de rodar direto na thread do request.
- **Self-host do Attendee**: se o uso crescer e a instância hospedada
  ficar cara/limitada, o Attendee é open source — dá pra rodar via
  Docker (`docker-compose`) no seu próprio servidor gratuito (ex: Oracle
  Cloud Free Tier). Instruções em https://github.com/attendee-labs/attendee.
- **Autenticação de usuários**: hoje qualquer um que acessar `/meetings`
  pode criar um bot. Pra um produto real, adicionar login e associar cada
  reunião a um usuário.
- **Aviso de gravação**: lembre os participantes que a reunião está sendo
  transcrita — é exigido em vários lugares.
