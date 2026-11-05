"""预设 demo repo 的最小可用内容。

会话初始化时由 ``WorkspaceService.ensure_container`` 写入会话 workspace。
文件路径均为相对 workspace 的相对路径，写入时仍会经过 ``validate_workspace_path``。
"""

DEMO_REPOS: dict[str, dict[str, str]] = {
    "todo-app": {
        "package.json": """{
  "name": "todo-app",
  "version": "1.0.0",
  "private": true,
  "main": "src/index.js",
  "scripts": {
    "start": "node src/index.js",
    "check": "node src/check.js"
  }
}
""",
        "src/index.js": """const todos = [];

function addTodo(title) {
  todos.push({ title, done: false });
  return todos;
}

function listTodos() {
  return todos.map((todo, index) => `${index + 1}. ${todo.done ? '[x]' : '[ ]'} ${todo.title}`);
}

console.log(listTodos());
module.exports = { addTodo, listTodos, todos };
""",
        "src/check.js": """const { addTodo, listTodos } = require('./index');

addTodo('write tests');
addTodo('ship the app');
console.log(listTodos().join('\\n'));
""",
        "README.md": """# todo-app

最小 Todo demo（Node.js，无第三方依赖）。

```bash
npm start
npm run check
```
""",
    },
    "express-demo": {
        "package.json": """{
  "name": "express-demo",
  "version": "1.0.0",
  "private": true,
  "main": "server.js",
  "scripts": {
    "start": "node server.js"
  },
  "dependencies": {
    "express": "^4.19.2"
  }
}
""",
        "server.js": """const express = require('express');
const todosRouter = require('./routes/todos');

const app = express();
app.use(express.json());
app.use('/todos', todosRouter);

app.get('/health', (req, res) => res.json({ ok: true }));

app.listen(3000, () => console.log('express-demo listening on :3000'));
""",
        "routes/todos.js": """const express = require('express');
const router = express.Router();

const todos = [];

router.get('/', (req, res) => res.json(todos));
router.post('/', (req, res) => {
  const todo = { title: req.body.title, done: false };
  todos.push(todo);
  res.status(201).json(todo);
});

module.exports = router;
""",
        "README.md": """# express-demo

最小 Express demo：`GET /health`、`GET/POST /todos`。

```bash
npm install
npm start
```
""",
    },
    "python-flask-demo": {
        "app.py": """from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
todos = []


@app.get("/health")
def health():
    return jsonify(ok=True)


@app.get("/")
def index():
    return render_template("index.html", todos=todos)


@app.post("/todos")
def create_todo():
    todo = {"title": request.json.get("title", ""), "done": False}
    todos.append(todo)
    return jsonify(todo), 201


if __name__ == "__main__":
    app.run(port=5000, debug=True)
""",
        "requirements.txt": """flask>=3.0
""",
        "templates/index.html": """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>python-flask-demo</title>
  </head>
  <body>
    <h1>Todos</h1>
    <ul>
      {% for todo in todos %}
      <li>{{ todo.title }}</li>
      {% endfor %}
    </ul>
  </body>
</html>
""",
        "README.md": """# python-flask-demo

最小 Flask demo。

```bash
pip install -r requirements.txt
python app.py
```
""",
    },
}

DEFAULT_DEMO_REPO = "todo-app"


def list_demo_repos() -> list[str]:
    return list(DEMO_REPOS)


def get_demo_repo(name: str) -> dict[str, str]:
    try:
        return DEMO_REPOS[name]
    except KeyError:
        available = ", ".join(DEMO_REPOS)
        raise ValueError(f"unknown demo repo: {name!r} (available: {available})") from None
