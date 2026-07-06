# SPDX-License-Identifier: Apache-2.0
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lex_automata ./lex_automata
COPY SKILL.md .
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn lex_automata.app:app --host 0.0.0.0 --port ${PORT}"]
