# CMIOT RAG

团队开发建议使用前后端分离模式：

- 后端目录：`knowledge_base_backend`
- 前端目录：`knowledge_base_frontend`
- 一键启动脚本：`scripts/start-dev.ps1`

## 首次拉取后的准备

1. 安装 `Git`
2. 安装 `Python 3.12`
3. 安装 `Node.js 20+`
4. 拉取仓库主分支

```powershell
git clone https://cnb.cool/CMIOT2026/CMIOT-rag.git
cd CMIOT-rag
```

## 本地配置

后端使用 `knowledge_base_backend/.env` 读取模型配置。仓库提供了示例文件：

```powershell
Copy-Item .\knowledge_base_backend\.env.example .\knowledge_base_backend\.env
```

至少需要把 `OPENAI_API_KEY` 改成你自己的有效密钥。

## 一键启动

在仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

脚本会自动：

- 检查并创建后端虚拟环境 `.venv`
- 安装后端依赖
- 安装前端依赖
- 在两个新终端窗口中分别启动后端和前端

如果依赖已经装好，想跳过重复安装，可以运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1 -SkipInstall
```

启动后访问：

- 前端开发环境：`http://127.0.0.1:5173`
- 后端接口：`http://127.0.0.1:8000`
- Swagger：`http://127.0.0.1:8000/docs`

## 一键关闭

在仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop-dev.ps1
```

脚本会按端口查找并关闭：

- 后端开发服务 `127.0.0.1:8000`
- 前端开发服务 `127.0.0.1:5173`

它会连同对应的子进程一起结束，适合团队日常启动和收尾。

## 手动启动

如果你想分别控制前后端，可以手动执行：

```powershell
cd .\knowledge_base_backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
cd .\knowledge_base_frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

## Git 约定

以下内容是本地运行产物，不应该提交：

- `.env`
- `.venv`
- `node_modules`
- 后端日志文件
- SQLite / Chroma 运行数据
- 后端 `static` 构建产物

因此，同事 `pull` 完代码后，需要自己在本地重新生成这些内容。这不会影响项目运行。
