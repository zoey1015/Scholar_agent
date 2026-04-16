#!/bin/bash
# ScholarAgent v4 部署脚本
# 用法: cd scholar-agent && bash scholar-v4/setup.sh
set -e

echo "=== ScholarAgent v4 部署 ==="
echo ""

# 1. 创建目录
echo "[1/5] 创建目录结构..."
mkdir -p backend/graph
mkdir -p backend/services
mkdir -p backend/tasks
mkdir -p backend/db

# 2. 复制文件
echo "[2/5] 复制文件..."

# 数据层
cp scholar-v4/db/migrations.py         backend/db/migrations.py

# 服务层
cp scholar-v4/services/claims_service.py         backend/services/claims_service.py
cp scholar-v4/services/relations_service.py      backend/services/relations_service.py
cp scholar-v4/services/research_state_service.py backend/services/research_state_service.py

# 图模块
cp scholar-v4/graph/__init__.py  backend/graph/__init__.py
cp scholar-v4/graph/state.py     backend/graph/state.py
cp scholar-v4/graph/nodes.py     backend/graph/nodes.py
cp scholar-v4/graph/graph.py     backend/graph/graph.py

# API
cp scholar-v4/api/research.py    backend/api/research.py

# Celery 任务
cp scholar-v4/tasks/analysis_tasks.py  backend/tasks/analysis_tasks.py
cp scholar-v4/tasks/upload_hook.py     backend/tasks/upload_hook.py

# 入口
cp scholar-v4/main.py            backend/main.py

# 前端
cp scholar-v4/frontend/pages/research.py   frontend/pages/research.py
cp scholar-v4/frontend/pages/dashboard.py  frontend/pages/dashboard.py

echo "  ✅ 文件复制完成"

# 3. 安装依赖
echo "[3/5] 安装 langgraph..."
sudo docker-compose exec -T backend pip install langgraph --break-system-packages -q 2>/dev/null && \
  echo "  ✅ langgraph 安装成功" || \
  echo "  ⚠️ 容器安装失败，请手动安装（见下方说明）"

sudo docker-compose exec -T celery-worker pip install langgraph --break-system-packages -q 2>/dev/null || true

# 4. 确保 services/__init__.py 和 tasks/__init__.py 存在
touch backend/services/__init__.py
touch backend/tasks/__init__.py

# 5. 重启服务
echo "[4/5] 重启服务..."
sudo docker-compose restart backend frontend

echo "[5/5] 等待服务就绪..."
sleep 5

# 验证
echo ""
echo "=== 验证 ==="
curl -s http://localhost:8000/ 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  后端未就绪（可能还在启动）"

echo ""
echo "=== 部署完成 ==="
echo ""
echo "新增页面："
echo "  📄 深度研究: http://localhost:8501 → 左侧选择 research"
echo "  📊 研究看板: http://localhost:8501 → 左侧选择 dashboard"
echo ""
echo "API 文档:"
echo "  POST /api/v1/research/stream  — SSE 流式深度研究"
echo "  POST /api/v1/research/run     — 非流式深度研究"
echo "  GET  /api/v1/research/dashboard — 研究看板数据"
echo ""
echo "集成上传管线（在现有 documents API 中添加）："
echo "  from backend.tasks.upload_hook import on_document_ready"
echo "  on_document_ready(document_id, user_id)"
echo ""
echo "如果 langgraph 安装失败："
echo "  echo 'langgraph' >> requirements.txt"
echo "  sudo docker-compose build backend"
echo "  sudo docker-compose up -d"
