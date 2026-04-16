"""
ScholarAgent LangGraph 模块

基于 Plan-Execute-Replan 架构：
- 图只负责数据收集（Researcher 工具调用）
- Synthesizer 和 Evaluator 在图外的 SSE 层处理
"""
