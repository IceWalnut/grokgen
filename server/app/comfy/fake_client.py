"""测试用的 `ComfyClient` 替身。

开发机上没有 GPU、也连不到 ComfyUI，网关除 `http_client` 之外的全部逻辑都靠它来测。
这是架构文档 §3 那条分层约束能成立的前提。
"""

from dataclasses import dataclass, field

from app.comfy.client import (
    ComfyUnreachable,
    ComfyValidationError,
    GpuStats,
    JobRecord,
    OutputFile,
    QueueState,
    UploadedImage,
)

# 取自 2026-09-22 服务器实测读数，便于与真实响应对照。
DEFAULT_STATS = GpuStats(
    name="cuda:0 NVIDIA GeForce RTX 4080 SUPER : cudaMallocAsync",
    vram_total_bytes=17170956288,
    vram_free_bytes=15647768576,
)


@dataclass
class _FakePrompt:
    """受控模式下，替身为每个 `prompt_id` 维护的一份模拟状态。

    Attributes:
        workflow: 提交上来的节点图，供断言。
        phase: `pending`（在 ComfyUI 队列里排着）/ `running`（正在跑）/
            `finished`（已结束，`history` 能查到记录了）。
        record: `phase` 为 `finished` 时 `history` 要返回的记录。
    """

    workflow: dict
    phase: str = "pending"
    record: JobRecord | None = None


class FakeComfyClient:
    """可控的 ComfyUI 替身。

    有两种模式：

    **默认模式**（`gated=False`）：`submit` 永远返回同一个 `prompt_id`，
    `history` 无视 `prompt_id` 返回构造时给的那条记录。够用于测单次调用的路径。

    **受控模式**（`gated=True`）：每次 `submit` 分配一个新的 `prompt_id`，
    `history` 按 id 查表，任务停在「还没结束」直到测试**显式**放行。
    这是测任务队列要用的模式 —— 它让「任务什么时候完成」变成测试能精确指定的事，
    而不是靠 sleep 去赌。

    ⚠️ 两种模式的区别只在 `submit` / `history` / `queue` 上，
    其余方法行为一致。默认模式保持与 M1R4 时完全相同，现有测试不受影响。
    """

    def __init__(
        self,
        stats: GpuStats | None = None,
        unreachable_reason: str | None = None,
        object_info: dict | None = None,
        reject_with: dict | None = None,
        record: JobRecord | None = None,
        rename_upload_to: str | None = None,
        *,
        gated: bool = False,
        serial_guard: bool = True,
    ) -> None:
        """构造替身。

        Args:
            stats: `system_stats` 要返回的读数。默认 `DEFAULT_STATS`。
            unreachable_reason: 不为 `None` 时所有调用都抛 `ComfyUnreachable`。
            object_info: `object_info` 要返回的节点定义。
            reject_with: 不为 `None` 时 `submit` 抛 `ComfyValidationError`，
                并把这个 dict 当作 `node_errors`。用来测「失败原因要带回来」。
            record: `history` 要返回的记录。`None` 表示任务还在排队。
                **只在默认模式下有意义**，受控模式按 `prompt_id` 查表。
            rename_upload_to: 不为 `None` 时，`upload_image` 返回这个名字而不是
                传进去的那个 —— 用来模拟 ComfyUI 的重名改名行为。
            gated: 开启受控模式，见类 docstring。
            serial_guard: 受控模式下，上一个任务还没结束就又来一次 `submit` 时
                直接抛 `AssertionError`。**这是替身在替 ComfyUI 说话**：
                显存只够一个重任务，网关不该这么做。
                它是串行判据的第二道、且**不依赖网关自己记账**的证据 ——
                即使网关的转移日志写错了，越界提交也会在这个边界上炸。
        """
        self._stats = stats or DEFAULT_STATS
        self._unreachable_reason = unreachable_reason
        self._object_info = object_info or {}
        self._reject_with = reject_with
        self._record = record
        self._rename_upload_to = rename_upload_to
        self.submitted: list[dict] = []
        self.uploads: list[tuple[str, str]] = []

        self._gated = gated
        self._serial_guard = serial_guard
        # prompt_id → 模拟状态。只在受控模式下有内容。
        self._prompts: dict[str, _FakePrompt] = {}
        self.prompt_ids: list[str] = []
        # `interrupt` 被调了几次。取消的判据要断言「该打的时候打了、不该打的时候没打」。
        self.interrupts: int = 0
        self.deleted_from_queue: list[str] = []
        # 任何时刻「已提交但还没结束」的任务数的峰值。串行判据断言它等于 1。
        self.max_outstanding_submits: int = 0

    def _guard(self) -> None:
        """不可达模式下统一抛异常。"""
        if self._unreachable_reason is not None:
            raise ComfyUnreachable(self._unreachable_reason)

    async def aclose(self) -> None:
        """替身没有连接要关，空实现。"""

    async def system_stats(self) -> GpuStats:
        """返回预置读数，或按构造参数抛不可达。

        Returns:
            构造时给定的 `GpuStats`。

        Raises:
            ComfyUnreachable: 构造时给了 `unreachable_reason`。
        """
        self._guard()
        return self._stats

    async def object_info(self) -> dict:
        """返回预置的节点定义。

        Returns:
            构造时给定的 `object_info`。

        Raises:
            ComfyUnreachable: 构造时给了 `unreachable_reason`。
        """
        self._guard()
        return self._object_info

    async def submit(self, workflow: dict, client_id: str) -> str:
        """记下提交的图并返回 `prompt_id`，或按构造参数拒绝。

        Args:
            workflow: 节点图，会被存进 `self.submitted` 供断言。
            client_id: 忽略。

        Returns:
            默认模式下是固定的 `"fake-prompt-id"`；
            受控模式下是 `fake-prompt-0000`、`fake-prompt-0001`…… 递增的新 id。

        Raises:
            ComfyValidationError: 构造时给了 `reject_with`。
            ComfyUnreachable: 构造时给了 `unreachable_reason`。
            AssertionError: 受控模式且开了 `serial_guard`，而上一个提交的任务
                还没结束 —— 那意味着网关违反了「同一时间只有一个重 GPU 任务」。
        """
        self._guard()
        if self._reject_with is not None:
            raise ComfyValidationError("Prompt outputs failed validation", self._reject_with)
        self.submitted.append(workflow)
        if not self._gated:
            return "fake-prompt-id"

        outstanding = [
            pid for pid, p in self._prompts.items() if p.phase != "finished"
        ]
        self.max_outstanding_submits = max(
            self.max_outstanding_submits, len(outstanding) + 1
        )
        if self._serial_guard and outstanding:
            raise AssertionError(
                "上一个任务还没结束就又提交了一个，违反「同一时间只有一个重 GPU 任务」："
                f"仍未结束的是 {outstanding}"
            )

        prompt_id = f"fake-prompt-{len(self.prompt_ids):04d}"
        self.prompt_ids.append(prompt_id)
        self._prompts[prompt_id] = _FakePrompt(workflow=workflow)
        return prompt_id

    # ---- 以下是给测试用的驱动接口：同步、调用后立刻生效 ----
    #
    # ⚠️ 为什么是「测试翻转状态 + 轮询看到」，而不是「history 里 await 一个事件」：
    # `history` 必须**立刻**返回 `None`（那是「还没结束」的语义）。
    # 让它挂住就把网关的轮询循环变成了阻塞等待，测的就不是真实结构了。

    def begin_running(self, prompt_id: str) -> None:
        """让一个任务从「排队中」变成「正在跑」。

        Args:
            prompt_id: `submit` 返回的 id。
        """
        self._prompts[prompt_id].phase = "running"

    def complete(
        self, prompt_id: str, filename: str = "grokgen_00001_.mp4"
    ) -> None:
        """让一个任务成功结束，`history` 从此能查到它。

        Args:
            prompt_id: `submit` 返回的 id。
            filename: 产出的视频文件名。
        """
        prompt = self._prompts[prompt_id]
        prompt.phase = "finished"
        prompt.record = finished_record(filename=filename, prompt_id=prompt_id)

    def fail(
        self, prompt_id: str, status: str = "error", messages: list | None = None
    ) -> None:
        """让一个任务以失败结束。

        Args:
            prompt_id: `submit` 返回的 id。
            status: ComfyUI 的 `status_str`。成功是 `"success"`，这里默认给 `"error"`。
            messages: ComfyUI 的执行消息原文，失败原因要从这里带回去。
        """
        prompt = self._prompts[prompt_id]
        prompt.phase = "finished"
        prompt.record = JobRecord(
            prompt_id=prompt_id,
            status=status,
            completed=True,
            messages=messages or [],
        )

    async def upload_image(
        self, data: bytes, filename: str, subfolder: str
    ) -> UploadedImage:
        """记下上传参数并返回结果。

        Args:
            data: 忽略。
            filename: 期望文件名，会被记进 `self.uploads` 供断言。
            subfolder: 子目录，同上。

        Returns:
            `UploadedImage`；构造时给了 `rename_upload_to` 就用那个名字。

        Raises:
            ComfyUnreachable: 构造时给了 `unreachable_reason`。
        """
        self._guard()
        self.uploads.append((filename, subfolder))
        return UploadedImage(
            name=self._rename_upload_to or filename,
            subfolder=subfolder,
            size_bytes=len(data),
        )

    async def history(self, prompt_id: str) -> JobRecord | None:
        """返回任务记录。

        Args:
            prompt_id: 受控模式下用它查表；默认模式下忽略。

        Returns:
            默认模式：构造时给定的 `JobRecord`，给的是 `None` 表示还没结束。
            受控模式：该任务已被 `complete()` / `fail()` 放行才有记录，
            否则返回 `None` —— 对应 ComfyUI「只在任务结束后才写 history」的真实行为。

        Raises:
            ComfyUnreachable: 构造时给了 `unreachable_reason`。
        """
        self._guard()
        if not self._gated:
            return self._record
        prompt = self._prompts.get(prompt_id)
        return prompt.record if prompt is not None else None

    async def queue(self) -> QueueState:
        """按各任务当前的模拟阶段回报队列快照。

        Returns:
            受控模式下，`phase` 为 `running` 的进 `running`、
            `pending` 的进 `pending`；默认模式下两个都是空的
            （默认模式不模拟队列，`running` 状态在那种模式下进不去）。

        Raises:
            ComfyUnreachable: 构造时给了 `unreachable_reason`。
        """
        self._guard()
        running = tuple(
            pid for pid, p in self._prompts.items() if p.phase == "running"
        )
        pending = tuple(
            pid for pid, p in self._prompts.items() if p.phase == "pending"
        )
        return QueueState(running=running, pending=pending)

    async def interrupt(self) -> None:
        """记一次中断调用，并把正在跑的那个任务标成「被中断地结束了」。

        模拟的是 ComfyUI 的真实行为：被中断的任务**仍然会在 history 里留下记录**，
        只是 `status_str` 不是 `success`。网关靠这一点把它落成 `cancelled`。

        Returns:
            无。

        Raises:
            ComfyUnreachable: 构造时给了 `unreachable_reason`。
        """
        self._guard()
        self.interrupts += 1
        for prompt_id, prompt in self._prompts.items():
            if prompt.phase == "running":
                self.fail(
                    prompt_id,
                    status="error",
                    messages=[["execution_interrupted", {"prompt_id": prompt_id}]],
                )
                break

    async def delete_queued(self, prompt_id: str) -> None:
        """把一个排队中的任务从队列里删掉。

        Args:
            prompt_id: 要删的 id。删一个不在排队的 id 不算错误，
                与 ComfyUI 的行为一致 —— 只是没有效果。

        Returns:
            无。

        Raises:
            ComfyUnreachable: 构造时给了 `unreachable_reason`。
        """
        self._guard()
        self.deleted_from_queue.append(prompt_id)
        prompt = self._prompts.get(prompt_id)
        if prompt is not None and prompt.phase == "pending":
            self.fail(
                prompt_id,
                status="error",
                messages=[["execution_cached", {"prompt_id": prompt_id}]],
            )


def finished_record(
    filename: str = "grokgen_00001_.mp4", prompt_id: str = "fake-prompt-id"
) -> JobRecord:
    """造一条「已成功」的任务记录，省得每个测试自己拼。

    Args:
        filename: 产出的文件名。
        prompt_id: 这条记录属于哪个任务。受控模式下每个任务的 id 不同，
            **必须传对** —— 记录里的 id 和查询用的 id 对不上时，
            网关拿到的会是另一个任务的产物，而那不会报错。

    Returns:
        一条 `status="success"` 且带一个视频输出的 `JobRecord`。
    """
    return JobRecord(
        prompt_id=prompt_id,
        status="success",
        completed=True,
        outputs=[OutputFile(filename=filename, subfolder="video", node_id="save_video")],
    )
