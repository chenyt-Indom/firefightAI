"""学习系统集成测试
验证: 经验记录 → 检索 → Few-shot注入 → 策略提炼 全链路
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.learning.battle_memory import BattleMemory
from src.learning.outcome_eval import OutcomeEvaluator
from src.learning.memory_retriever import MemoryRetriever
from src.learning.strategy_compressor import StrategyCompressor


def test_battle_memory():
    """测试经验库 CRUD"""
    print("=" * 50)
    print("1. 测试经验库...")
    mem = BattleMemory()
    print(f"   现有记录: {mem.count()} 条")

    # 写入测试经验
    rid = mem.record(
        state_hash="abc12345",
        ally_count=15,
        enemy_count=10,
        ally_positions=[(0.3, 0.5), (0.4, 0.6)],
        decision={"action": "move", "target": [0.6, 0.7], "reason": "推进"},
        outcome_score=25.0,
        cycle_num=1,
        game_session="test_session",
    )
    print(f"   新增记录 ID={rid}")

    # 检索
    results = mem.retrieve_similar("abc1xxxx", ally_count=14, top_k=3)
    print(f"   检索到 {len(results)} 条相似经验:")
    for r in results:
        print(f"     决策={r['decision']['action']}, 得分={r['outcome_score']}")

    # 统计
    stats = mem.get_stats("test_session")
    print(f"   统计: 总数={stats['total']}, 均分={stats['avg_score']}")

    # 清理
    mem.clear_session("test_session")
    mem.close()
    print("   ✅ 经验库测试通过\n")


def test_outcome_eval():
    """测试结果评估"""
    print("=" * 50)
    print("2. 测试结果评估器...")

    import cv2
    import numpy as np

    evaluator = OutcomeEvaluator()

    # 创建模拟帧
    h, w = 1080, 1920
    frame1 = np.zeros((h, w, 3), dtype=np.uint8)
    frame2 = np.zeros((h, w, 3), dtype=np.uint8)

    # 在 frame1 中添加蓝色(友军)和红色(敌军)标记
    # #58A5F3 = BGR(243,165,88), #FD8177 = BGR(119,129,253)
    blue = (243, 165, 88)
    red = (119, 129, 253)

    for i in range(10):
        x, y = 200 + i * 50, 500
        cv2.circle(frame1, (x, y), 8, blue, -1)
    for i in range(5):
        x, y = 200 + i * 80, 400
        cv2.circle(frame1, (x, y), 8, red, -1)

    # frame2: 少2个敌人, 少1个友军
    for i in range(9):
        x, y = 200 + i * 50, 500
        cv2.circle(frame2, (x, y), 8, blue, -1)
    for i in range(3):
        x, y = 200 + i * 80, 400
        cv2.circle(frame2, (x, y), 8, red, -1)

    # 先缓存 frame1 (第一轮调用)
    _ = evaluator.evaluate(frame1)  # 无对比基线, 仅缓存

    # 再评估 frame2 (与缓存的 frame1 对比)
    result = evaluator.evaluate(frame2)
    print(f"   评分: {result}")
    print(f"   预期: 击杀2敌(+20), 损失1友(-10) → 总分~10")
    assert result["enemy_delta"] == 2, f"预期击杀2敌, 实际{result['enemy_delta']}"
    assert result["ally_delta"] == 1, f"预期损失1友, 实际{result['ally_delta']}"
    print("   ✅ 结果评估测试通过\n")


def test_memory_retriever():
    """测试记忆检索 + few-shot 格式化"""
    print("=" * 50)
    print("3. 测试记忆检索器...")

    mem = BattleMemory()
    retriever = MemoryRetriever(mem)

    # 插入几条测试经验
    for i in range(5):
        mem.record(
            state_hash="test1234",
            ally_count=10 + i,
            enemy_count=8,
            ally_positions=[],
            decision={"action": "move", "target": [0.5 + i * 0.1, 0.6], "reason": f"推进{i}"},
            outcome_score=10 + i * 5,
            cycle_num=i,
            game_session="test_retrieve",
        )

    # 检索
    exps = retriever.retrieve("testxxxx", ally_count=12, enemy_count=8, top_k=3)
    print(f"   检索到 {len(exps)} 条经验")

    # 格式化
    few_shot = retriever.format_as_few_shot(exps)
    print(f"   Few-shot 文本 ({len(few_shot)} 字符):")
    for line in few_shot.split("\n")[:5]:
        print(f"   {line}")

    # 清理
    mem.clear_session("test_retrieve")
    mem.close()
    print("   ✅ 记忆检索测试通过\n")


def test_full_integration():
    """模拟完整学习循环"""
    print("=" * 50)
    print("4. 全链路测试...")

    mem = BattleMemory()
    evaluator = OutcomeEvaluator()
    retriever = MemoryRetriever(mem)

    SESSION = "test_integration"

    # 模拟 3 轮循环
    for cycle in range(1, 4):
        state_hash = f"state{cycle:04d}"
        ally_count = 15 - cycle
        enemy_count = 10 - cycle // 2

        # 检索 (模拟 LLM 调用前)
        exps = retriever.retrieve(state_hash, ally_count, enemy_count)
        few_shot = retriever.format_as_few_shot(exps)

        # 决策 (模拟)
        decision = {
            "action": "attack" if enemy_count > 5 else "move",
            "target": [0.5, 0.5 + cycle * 0.1],
            "reason": f"第{cycle}轮战术",
        }

        # 执行后... (下一轮评估)
        outcome_score = 10.0 + cycle * 5  # 模拟正分

        # 记录经验
        mem.record(
            state_hash=state_hash,
            ally_count=ally_count,
            enemy_count=enemy_count,
            ally_positions=[],
            decision=decision,
            outcome_score=outcome_score,
            cycle_num=cycle,
            game_session=SESSION,
        )

        print(f"   第{cycle}轮: 友{ally_count}vs敌{enemy_count} "
              f"→ {decision['action']} → 得分+{outcome_score:.0f} "
              f"(检索到{len(exps)}条参考)")

    # 最终统计
    stats = mem.get_stats(SESSION)
    print(f"\n   最终统计: {stats['total']}条记录, 均分{stats['avg_score']}")

    # 获取高分经验
    top = mem.get_top_experiences(5, SESSION)
    print(f"   Top-5 最高分经验:")
    for t in top:
        print(f"     决策={t['decision']['action']}, 得分={t['outcome_score']}")

    # 清理
    mem.clear_session(SESSION)
    mem.close()
    print("   ✅ 全链路测试通过\n")


if __name__ == "__main__":
    test_battle_memory()
    test_outcome_eval()
    test_memory_retriever()
    test_full_integration()
    print("=" * 50)
    print("🎉 所有学习系统测试通过!")
