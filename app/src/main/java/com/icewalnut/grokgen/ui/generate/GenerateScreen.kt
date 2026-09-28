package com.icewalnut.grokgen.ui.generate

import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.icewalnut.grokgen.model.FirstFrameImage
import com.icewalnut.grokgen.model.FormProblem
import com.icewalnut.grokgen.model.GenerateForm
import com.icewalnut.grokgen.model.GenerateMode
import com.icewalnut.grokgen.model.formatSecondsForChip
import com.icewalnut.grokgen.model.summarizeNormalized
import com.icewalnut.grokgen.model.validate
import com.icewalnut.grokgen.net.SubmitOutcome
import com.icewalnut.grokgen.ui.component.UriThumbnail
import com.icewalnut.grokgen.ui.component.adviceFor
import com.icewalnut.grokgen.ui.theme.GrokgenTheme

/**
 * 视频生成页。需求 F2，视觉稿见 App 架构文档 §9.5.0 的「生成页」画板。
 *
 * M2 只做 T2VA / I2VA、三段 prompt、时长、Turbo、种子（App 架构文档 §4）。
 * FL2VA、LoRA、高级参数都不做。
 *
 * ⭐ 提交之后把网关**实际用的**参数显示出来（VS-35）—— 契约 §2 写明它不是装饰：
 * 用户填 5 秒实际得到 5.17 秒、画布是按首帧图比例推的、seed 是网关生成的。
 * 不显示的话用户会以为模型不准，或者永远复现不出某个结果。
 */
@Composable
fun GenerateScreen(
    state: GenerateUiState,
    onFormChange: ((GenerateForm) -> GenerateForm) -> Unit,
    onPickFirstFrame: () -> Unit,
    onSubmit: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val spacing = GrokgenTheme.spacing
    val colors = MaterialTheme.colorScheme
    val form = state.form
    val submitting = state.phase is SubmitPhase.Submitting

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(colors.background)
            .systemBarsPadding()
            .verticalScroll(rememberScrollState()),
    ) {
        Header(onBack = onBack)

        Spacer(Modifier.height(spacing.md))
        ModeSwitch(
            mode = form.mode,
            onModeChange = { mode -> onFormChange { it.copy(mode = mode) } },
            modifier = Modifier.padding(horizontal = spacing.gutter),
        )

        // ⚠️ 切到文生视频时只是**不显示**首帧图，表单里的图留着 ——
        //    切回来还在。发请求时按模式决定带不带它（toSubmitRequest）。
        if (form.mode == GenerateMode.ImageToVideo) {
            Spacer(Modifier.height(spacing.lg))
            FirstFrameSlot(image = form.firstFrame, onPick = onPickFirstFrame)
        }

        Column(modifier = Modifier.padding(horizontal = spacing.gutter)) {
            Spacer(Modifier.height(spacing.xl))
            PromptField(
                label = "画面描述",
                value = form.description,
                onValueChange = { v -> onFormChange { it.copy(description = v) } },
                placeholder = "画面里发生了什么",
                singleLine = false,
            )
            Spacer(Modifier.height(spacing.lg))
            PromptField(
                label = "环境声",
                value = form.soundscape,
                onValueChange = { v -> onFormChange { it.copy(soundscape = v) } },
                placeholder = "留空则不指定",
            )
            Spacer(Modifier.height(spacing.lg))
            PromptField(
                label = "背景音乐",
                value = form.music,
                onValueChange = { v -> onFormChange { it.copy(music = v) } },
                placeholder = "留空则不生成音乐",
            )

            Spacer(Modifier.height(spacing.xl))
            ParameterChips(form = form, onFormChange = onFormChange)

            Spacer(Modifier.height(spacing.xl))
            SubmitArea(
                problem = validate(form),
                submitting = submitting,
                onSubmit = onSubmit,
            )

            (state.phase as? SubmitPhase.Finished)?.let { finished ->
                Spacer(Modifier.height(spacing.xl))
                SubmitResult(finished)
            }
        }

        Spacer(Modifier.height(spacing.xxl))
    }
}

@Composable
private fun Header(onBack: () -> Unit) {
    val spacing = GrokgenTheme.spacing
    val colors = MaterialTheme.colorScheme
    Row(
        modifier = Modifier.padding(start = spacing.sm, top = spacing.sm, end = spacing.gutter),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .size(44.dp)
                .clip(RoundedCornerShape(12.dp))
                .clickable(onClick = onBack),
            contentAlignment = Alignment.Center,
        ) {
            Text("←", style = MaterialTheme.typography.titleLarge, color = colors.onBackground)
        }
        Text(
            text = "新建视频",
            style = MaterialTheme.typography.headlineSmall,
            color = colors.onBackground,
        )
    }
}

/** 「文生视频 / 图生视频」分段切换。选中那一格靠底色**和字重**区分，不只靠颜色。 */
@Composable
private fun ModeSwitch(
    mode: GenerateMode,
    onModeChange: (GenerateMode) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(colors.surface)
            .padding(spacing.xs),
        horizontalArrangement = Arrangement.spacedBy(spacing.xs),
    ) {
        listOf(GenerateMode.TextToVideo to "文生视频", GenerateMode.ImageToVideo to "图生视频")
            .forEach { (option, label) ->
                val selected = option == mode
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .height(40.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .background(if (selected) colors.outline else Color.Transparent)
                        .clickable { onModeChange(option) },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text = label,
                        style = if (selected) MaterialTheme.typography.titleSmall else MaterialTheme.typography.bodyMedium,
                        color = if (selected) colors.onBackground else colors.onSurfaceVariant,
                    )
                }
            }
    }
}

/**
 * 首帧图槽。
 *
 * ⚠️ 没选图时是一块占位；选了之后**铺满宽度、按原比例显示**（`ContentScale.Fit`）——
 * 画布是按这张图的比例推的，App 里看到的比例就应当是视频的比例。
 * Crop 会让用户看到一个和成片不一样的构图。
 */
@Composable
private fun FirstFrameSlot(image: FirstFrameImage?, onPick: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing

    if (image == null) {
        Box(
            modifier = Modifier
                .padding(horizontal = spacing.gutter)
                .fillMaxWidth()
                .aspectRatio(16f / 9f)
                .clip(RoundedCornerShape(16.dp))
                .background(colors.surface)
                .clickable(onClick = onPick),
            contentAlignment = Alignment.Center,
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("选择首帧图", style = MaterialTheme.typography.titleMedium, color = colors.onBackground)
                Spacer(Modifier.height(spacing.xs))
                Text(
                    "视频的第一帧就是这张图",
                    style = MaterialTheme.typography.bodySmall,
                    color = colors.onSurfaceVariant,
                )
            }
        }
        return
    }

    val ratio = if (image.width > 0 && image.height > 0) image.width.toFloat() / image.height else 16f / 9f
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .aspectRatio(ratio)
            .background(colors.surface),
    ) {
        UriThumbnail(uri = Uri.parse(image.uri), contentScale = ContentScale.Fit)

        // 左下角：网关读到的尺寸。画布按它推，所以显示它而不是本机读数。
        Row(
            modifier = Modifier
                .align(Alignment.BottomStart)
                .padding(spacing.lg)
                .clip(RoundedCornerShape(10.dp))
                .background(colors.background.copy(alpha = 0.62f))
                .padding(horizontal = spacing.md, vertical = spacing.xs),
            horizontalArrangement = Arrangement.spacedBy(spacing.sm),
        ) {
            Text("首帧图", style = MaterialTheme.typography.labelSmall, color = colors.onBackground)
            Text(
                "${image.width} × ${image.height}",
                style = MaterialTheme.typography.labelSmall,
                color = colors.onSurfaceVariant,
            )
        }

        Box(
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(end = spacing.lg, bottom = spacing.sm)
                .height(44.dp)
                .clip(RoundedCornerShape(12.dp))
                .background(colors.background.copy(alpha = 0.62f))
                .clickable(onClick = onPick)
                .padding(horizontal = spacing.md),
            contentAlignment = Alignment.Center,
        ) {
            Text("更换", style = MaterialTheme.typography.bodyMedium, color = colors.onBackground)
        }
    }
}

/**
 * 带中文标签的下划线输入框。
 *
 * ⚠️ **不把英文键名甩给用户**（需求 F2）—— 画面描述 / 环境声 / 背景音乐，
 * 对应的 `integrated_multimodal_description` 这些名字只出现在网关里。
 */
@Composable
private fun PromptField(
    label: String,
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    singleLine: Boolean = true,
    keyboardType: KeyboardType = KeyboardType.Text,
) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    Column {
        Text(label, style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
        Spacer(Modifier.height(spacing.xs))
        TextField(
            value = value,
            onValueChange = onValueChange,
            singleLine = singleLine,
            minLines = if (singleLine) 1 else 2,
            textStyle = MaterialTheme.typography.bodyLarge,
            placeholder = { Text(placeholder, style = MaterialTheme.typography.bodyLarge) },
            keyboardOptions = KeyboardOptions(
                keyboardType = keyboardType,
                imeAction = if (singleLine) ImeAction.Next else ImeAction.Default,
            ),
            colors = TextFieldDefaults.colors(
                focusedContainerColor = Color.Transparent,
                unfocusedContainerColor = Color.Transparent,
                disabledContainerColor = Color.Transparent,
                focusedTextColor = colors.onBackground,
                unfocusedTextColor = colors.onBackground,
                focusedPlaceholderColor = colors.onSurfaceVariant,
                unfocusedPlaceholderColor = colors.onSurfaceVariant,
                cursorColor = colors.primary,
                focusedIndicatorColor = colors.primary,
                unfocusedIndicatorColor = colors.outline,
            ),
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

/** 时长 / Turbo / 种子 三个小按钮，外加种子展开后的输入框。 */
@Composable
private fun ParameterChips(
    form: GenerateForm,
    onFormChange: ((GenerateForm) -> GenerateForm) -> Unit,
) {
    val spacing = GrokgenTheme.spacing
    val colors = MaterialTheme.colorScheme
    var editingSeed by rememberSaveable { mutableStateOf(form.seedInput.isNotBlank()) }

    Row(horizontalArrangement = Arrangement.spacedBy(spacing.sm)) {
        // ⚠️ 只显示**请求**的秒数，不在本地预测实际秒数（见 DurationPreset 的说明）。
        Chip(onClick = { onFormChange { it.copy(duration = it.duration.next()) } }) {
            Text("时长", style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
            Text(
                "${formatSecondsForChip(form.duration.seconds)} 秒",
                style = MaterialTheme.typography.bodySmall,
                color = colors.onBackground,
            )
        }

        // 状态用**文字**说出来（Turbo / 标准），圆点只是辅助 —— 不只靠颜色（需求 §7.1.3）。
        Chip(onClick = { onFormChange { it.copy(turbo = !it.turbo) } }) {
            Box(
                modifier = Modifier
                    .size(7.dp)
                    .clip(RoundedCornerShape(4.dp))
                    .background(if (form.turbo) colors.primary else colors.outline),
            )
            Text(
                if (form.turbo) "Turbo" else "标准",
                style = MaterialTheme.typography.bodySmall,
                color = colors.onBackground,
            )
        }

        Chip(onClick = { editingSeed = !editingSeed }) {
            Text("种子", style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
            Text(
                form.seedInput.trim().ifEmpty { "随机" },
                style = MaterialTheme.typography.bodySmall,
                color = colors.onBackground,
                maxLines = 1,
            )
        }
    }

    if (editingSeed) {
        Spacer(Modifier.height(spacing.lg))
        PromptField(
            label = "种子（留空则由服务器随机取一个，并在提交后告诉你）",
            value = form.seedInput,
            onValueChange = { v -> onFormChange { it.copy(seedInput = v) } },
            placeholder = "随机",
            keyboardType = KeyboardType.Number,
        )
    }

    // Turbo 不是一个开关而是一整套采样参数（契约 §2），说一句它换掉了什么。
    Spacer(Modifier.height(spacing.sm))
    Text(
        // 75 秒是 M1R7 在 4080 SUPER 上实测一次的读数（模型已在显存、5 秒视频）。
        // 标准档没有实测过，所以只说步数，不给时间也不评价画质。
        text = if (form.turbo) "Turbo：8 步，5 秒视频实测约 75 秒" else "标准：25 步，比 Turbo 慢（没有实测过具体多久）",
        style = MaterialTheme.typography.labelSmall,
        color = colors.onSurfaceVariant,
    )
}

@Composable
private fun Chip(onClick: () -> Unit, content: @Composable () -> Unit) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    Row(
        modifier = Modifier
            .heightIn(min = 44.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(colors.surface)
            .clickable(onClick = onClick)
            .padding(horizontal = spacing.md),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        content()
    }
}

@Composable
private fun SubmitArea(problem: FormProblem?, submitting: Boolean, onSubmit: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing

    // 按钮不可点时说清为什么，不让用户对着一个灰按钮猜。
    if (problem != null) {
        Text(
            text = describe(problem),
            style = MaterialTheme.typography.bodySmall,
            color = colors.onSurfaceVariant,
        )
        Spacer(Modifier.height(spacing.sm))
    }

    Button(
        onClick = onSubmit,
        enabled = problem == null && !submitting,
        shape = RoundedCornerShape(12.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = colors.primary,
            contentColor = colors.onPrimary,
            disabledContainerColor = colors.surfaceVariant,
            disabledContentColor = colors.onSurfaceVariant,
        ),
        modifier = Modifier.fillMaxWidth().height(52.dp),
    ) {
        if (submitting) {
            CircularProgressIndicator(
                modifier = Modifier.size(20.dp),
                strokeWidth = 2.dp,
                color = colors.onSurfaceVariant,
            )
        } else {
            Text("生成视频", style = MaterialTheme.typography.titleMedium)
        }
    }

    Spacer(Modifier.height(spacing.sm))
    Text(
        text = "同一时间只运行一个任务",
        style = MaterialTheme.typography.labelSmall,
        color = colors.onSurfaceVariant,
        textAlign = TextAlign.Center,
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
private fun SubmitResult(finished: SubmitPhase.Finished) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    val status = GrokgenTheme.status

    val outcome = finished.outcome
    if (outcome !is SubmitOutcome.Submitted) {
        val (title, detail) = describe(outcome)
        // 「不确定提交了没有」不是失败 —— 用「排队中」的灰而不是失败的红。
        val dot = if (outcome is SubmitOutcome.MaybeSubmitted) status.queued else status.failed
        ResultCard(title = title, detail = detail, dotColor = dot)
        return
    }

    val job = outcome.job
    ResultCard(
        title = "已提交 · ${job.jobId}",
        detail = "任务已进入队列。进度要到下一版的队列页才能看。",
        dotColor = status.queued,
    )

    Spacer(Modifier.height(spacing.xl))
    Text("网关实际使用的参数", style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
    Spacer(Modifier.height(spacing.sm))
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(colors.surface)
            .padding(horizontal = spacing.lg, vertical = spacing.xs),
    ) {
        val rows = summarizeNormalized(job.normalized, finished.turbo)
        rows.forEachIndexed { index, row ->
            Row(
                modifier = Modifier.fillMaxWidth().padding(vertical = spacing.md),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(row.label, style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
                Spacer(Modifier.size(spacing.lg))
                Text(
                    row.value,
                    style = MaterialTheme.typography.bodySmall,
                    color = colors.onBackground,
                    textAlign = TextAlign.End,
                )
            }
            if (index != rows.lastIndex) HorizontalDivider(color = colors.surfaceVariant)
        }
    }

    // ⚠️ notices 是网关生成的完整中文句子 —— **原文显示，不按前缀解析**（契约 §2）。
    if (job.notices.isNotEmpty()) {
        Spacer(Modifier.height(spacing.lg))
        Column(verticalArrangement = Arrangement.spacedBy(spacing.sm)) {
            job.notices.forEach { notice ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(12.dp))
                        .background(colors.surfaceVariant)
                        .padding(spacing.md),
                    horizontalArrangement = Arrangement.spacedBy(spacing.sm),
                ) {
                    Text("ⓘ", style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
                    Text(notice, style = MaterialTheme.typography.bodySmall, color = colors.onBackground)
                }
            }
        }
    }
}

@Composable
private fun ResultCard(title: String, detail: String?, dotColor: Color) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(colors.surface)
            .padding(spacing.lg),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(8.dp)
                    .clip(RoundedCornerShape(4.dp))
                    .background(dotColor),
            )
            Spacer(Modifier.size(spacing.sm))
            Text(title, style = MaterialTheme.typography.titleMedium, color = colors.onBackground)
        }
        if (detail != null) {
            Spacer(Modifier.height(spacing.md))
            Text(detail, style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
        }
    }
}

private fun describe(problem: FormProblem): String = when (problem) {
    FormProblem.MissingFirstFrame -> "图生视频需要先选一张首帧图"
    FormProblem.MissingDescription -> "先写一句画面描述"
    FormProblem.InvalidSeed -> "种子只能是不带符号的整数，或者留空让服务器随机"
}

/**
 * 每一档都要说清**该做什么**，不只是说失败了。
 * 传输层那几档复用连接页的文案（`ui/component/ConnectionAdvice.kt`）。
 */
private fun describe(outcome: SubmitOutcome): Pair<String, String?> = when (outcome) {
    is SubmitOutcome.Submitted -> "已提交 · ${outcome.job.jobId}" to null

    is SubmitOutcome.Unreachable ->
        adviceFor(outcome.reason).let { it.title to "任务没有提交出去，可以放心再试。\n${it.detail.orEmpty()}".trim() }

    // ⚠️ 不能说「失败」：请求可能已经到了网关、任务已经登记。
    //    说成失败，用户会再点一次 —— 两个任务、两次 GPU。
    SubmitOutcome.MaybeSubmitted ->
        "不确定是否已经提交" to
            "没有收到网关的应答。如果网关其实收到了请求，任务已经在排队了。" +
            "请先确认服务器上的队列里有没有这个任务，再决定要不要重新提交 —— " +
            "重复提交会让显卡再跑一遍。（App 里的队列页下一版才有。）"

    is SubmitOutcome.FirstFrameUnreadable ->
        "服务器读不出这张首帧图" to
            ((outcome.detail ?: "") + "\n它可能已经在服务器上被清理掉了。重新选一张图上传再试。").trim()

    is SubmitOutcome.MalformedRequest ->
        "请求形状不对" to "这是 App 的 bug，不是你填错了。${outcome.detail.orEmpty()}"

    is SubmitOutcome.GatewayClosing ->
        "网关正在关停，不收新任务" to (outcome.detail ?: "等网关重启完再提交。")

    is SubmitOutcome.HttpError ->
        "服务器回了 HTTP ${outcome.code}" to outcome.detail

    is SubmitOutcome.Unexpected ->
        "没见过的失败" to outcome.throwable.toString()
}
