package com.icewalnut.grokgen.ui.upload

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.icewalnut.grokgen.data.LocalImageFacts
import com.icewalnut.grokgen.net.UploadOutcome
import com.icewalnut.grokgen.net.dto.UploadedImageDto
import com.icewalnut.grokgen.ui.component.UriThumbnail
import com.icewalnut.grokgen.ui.component.adviceFor
import com.icewalnut.grokgen.ui.theme.GrokgenTheme

/**
 * 上传首帧图。需求 F6（三个来源里的第一个）。
 *
 * ⭐ 这一页产出的是一个 `asset_id` 字符串 —— 参考图的三个来源
 * （手机上传 / 媒体库挑 / SD 现生成）**在网关那里收敛成同一个东西**，
 * 所以 M3、M5 加进来时生成页一个字都不用改。
 */
@Composable
fun UploadScreen(
    state: UploadUiState,
    onPicked: (android.net.Uri) -> Unit,
    onUpload: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val spacing = GrokgenTheme.spacing
    val colors = MaterialTheme.colorScheme

    // ⚠️ Photo Picker：系统进程提供选择界面，只把选中那一张的 URI 交过来。
    //    **不申请 READ_MEDIA_IMAGES** —— 为「用户自己指一张图」去要整个相册的
    //    读权限，是多要了一个不必要的权限。
    val picker = rememberLauncherForActivityResult(
        ActivityResultContracts.PickVisualMedia()
    ) { uri -> uri?.let(onPicked) }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(colors.background)
            .systemBarsPadding()
            .verticalScroll(rememberScrollState()),
    ) {
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
                text = "上传首帧图",
                style = MaterialTheme.typography.headlineSmall,
                color = colors.onBackground,
            )
        }

        Spacer(Modifier.height(spacing.sm))
        Text(
            text = "选一张图传到服务器，拿到一个素材编号。生成视频时带上它，画面就从这张图开始。",
            style = MaterialTheme.typography.bodySmall,
            color = colors.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = spacing.gutter),
        )

        Spacer(Modifier.height(spacing.xl))

        PickedImageArea(
            state = state,
            onPick = {
                picker.launch(
                    PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)
                )
            },
        )

        Spacer(Modifier.height(spacing.lg))

        Column(modifier = Modifier.padding(horizontal = spacing.gutter)) {
            state.localFacts?.let { LocalFactsRow(it) }

            Spacer(Modifier.height(spacing.lg))

            when (val phase = state.phase) {
                UploadPhase.Transcoding -> TranscodingRow()
                is UploadPhase.Uploading -> UploadingRow(phase)
                else -> UploadButton(enabled = state.pickedUri != null, onUpload = onUpload)
            }

            state.outcome?.let {
                Spacer(Modifier.height(spacing.xl))
                UploadOutcomeCard(it, state.localFacts)
            }
        }

        Spacer(Modifier.height(spacing.xxl))
    }
}

@Composable
private fun PickedImageArea(state: UploadUiState, onPick: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    val thumbnail = state.pickedUri

    if (thumbnail == null) {
        Box(
            modifier = Modifier
                .padding(horizontal = spacing.gutter)
                .fillMaxWidth()
                .aspectRatio(16f / 10f)
                .clip(RoundedCornerShape(16.dp))
                .background(colors.surfaceVariant)
                .clickable(onClick = onPick),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = "从相册选一张图",
                style = MaterialTheme.typography.titleMedium,
                color = colors.onSurfaceVariant,
            )
        }
        return
    }

    // ⚠️ 媒体铺满宽度，不加外框（需求 §7.1.1：内容优先）。
    // ⚠️ ContentScale.Fit 而不是 Crop —— 这一页的全部意义就是让用户确认
    //    「传上去的是这张图、这个比例」。Crop 会让 App 里看着是方的、
    //    传上去是长的，而那正是这一页要防的误会。
    val ratio = state.localFacts
        ?.takeIf { it.width > 0 && it.height > 0 }
        ?.let { it.width.toFloat() / it.height.toFloat() }
        ?: (16f / 10f)

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .aspectRatio(ratio)
            .background(colors.surfaceVariant)
            .clickable(onClick = onPick),
    ) {
        UriThumbnail(uri = thumbnail, contentScale = ContentScale.Fit)
    }
}

@Composable
private fun LocalFactsRow(facts: LocalImageFacts) {
    val colors = MaterialTheme.colorScheme
    Text(
        text = buildString {
            append("本机读到 ${facts.width} × ${facts.height}")
            facts.mimeType?.let { append(" · $it") }
            if (facts.sizeBytes > 0) append(" · ${formatBytes(facts.sizeBytes)}")
        },
        style = MaterialTheme.typography.bodySmall,
        color = colors.onSurfaceVariant,
    )
}

@Composable
private fun UploadButton(enabled: Boolean, onUpload: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    Button(
        onClick = onUpload,
        enabled = enabled,
        shape = RoundedCornerShape(12.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = colors.primary,
            contentColor = colors.onPrimary,
            disabledContainerColor = colors.surfaceVariant,
            disabledContentColor = colors.onSurfaceVariant,
        ),
        modifier = Modifier
            .fillMaxWidth()
            .height(52.dp),
    ) {
        Text("上传", style = MaterialTheme.typography.titleMedium)
    }
}

@Composable
private fun TranscodingRow() {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    Column(modifier = Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(
                modifier = Modifier.size(18.dp),
                strokeWidth = 2.dp,
                color = colors.primary,
            )
            Spacer(Modifier.size(spacing.md))
            Text(
                text = "正在转码…",
                style = MaterialTheme.typography.titleMedium,
                color = colors.onBackground,
            )
        }
        Spacer(Modifier.height(spacing.sm))
        // ⚠️ 这一段单独显示，理由同 loading_model：解码一张 12 MP 的图要好几秒，
        //    和上传混成一个进度条会让人以为卡住了。
        Text(
            text = "这张图的格式服务器不认，先在手机上转成 JPEG。尺寸不会变。",
            style = MaterialTheme.typography.bodySmall,
            color = colors.onSurfaceVariant,
        )
    }
}

@Composable
private fun UploadingRow(phase: UploadPhase.Uploading) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    Column(modifier = Modifier.fillMaxWidth()) {
        if (phase.total > 0) {
            LinearProgressIndicator(
                progress = { (phase.sent.toFloat() / phase.total.toFloat()).coerceIn(0f, 1f) },
                color = colors.primary,
                trackColor = colors.surfaceVariant,
                modifier = Modifier.fillMaxWidth().height(3.dp),
            )
            Spacer(Modifier.height(spacing.sm))
            Text(
                text = "${formatBytes(phase.sent)} / ${formatBytes(phase.total)}",
                style = MaterialTheme.typography.labelSmall,
                color = colors.onSurfaceVariant,
            )
        } else {
            LinearProgressIndicator(
                color = colors.primary,
                trackColor = colors.surfaceVariant,
                modifier = Modifier.fillMaxWidth().height(3.dp),
            )
            Spacer(Modifier.height(spacing.sm))
            Text(
                text = "正在上传（读不出文件大小，没有百分比）",
                style = MaterialTheme.typography.labelSmall,
                color = colors.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun UploadOutcomeCard(outcome: UploadOutcome, localFacts: LocalImageFacts?) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    val status = GrokgenTheme.status

    val succeeded = outcome is UploadOutcome.Uploaded
    val (title, detail) = describe(outcome)

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
                    .background(if (succeeded) status.done else status.failed),
            )
            Spacer(Modifier.size(spacing.sm))
            Text(
                text = title,
                style = MaterialTheme.typography.titleMedium,
                color = colors.onBackground,
            )
        }

        if (detail != null) {
            Spacer(Modifier.height(spacing.md))
            Text(
                text = detail,
                style = MaterialTheme.typography.bodySmall,
                color = colors.onSurfaceVariant,
            )
        }

        if (outcome is UploadOutcome.Uploaded) {
            Spacer(Modifier.height(spacing.lg))
            HorizontalDivider(color = colors.outlineVariant)
            UploadedDetails(outcome.image, localFacts)
        }
    }
}

@Composable
private fun UploadedDetails(image: UploadedImageDto, localFacts: LocalImageFacts?) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing

    DetailRow("素材编号", image.assetId)
    DetailRow("服务器读到", "${image.width} × ${image.height}")
    DetailRow("大小", formatBytes(image.sizeBytes))

    // ⭐ 尺寸对照。本机和服务器用的是同一个口径（编码尺寸），所以能直接比。
    //    ⚠️ 两者不同意味着**视频画布会按服务器那组数推，首帧可能被拉伸** ——
    //    而那不会以任何形式报错。把它画在屏幕上，就不必等视频出来才发现。
    if (localFacts != null && localFacts.width > 0 && localFacts.height > 0) {
        val mismatch = localFacts.width != image.width || localFacts.height != image.height
        Spacer(Modifier.height(spacing.sm))
        Text(
            text = if (mismatch) {
                "⚠️ 本机读到 ${localFacts.width} × ${localFacts.height}，和服务器不一致 —— " +
                    "画布会按服务器那组数推，首帧可能被拉伸"
            } else {
                "本机与服务器读到的尺寸一致"
            },
            style = MaterialTheme.typography.bodySmall,
            color = if (mismatch) GrokgenTheme.status.failed else colors.onSurfaceVariant,
            textAlign = TextAlign.Start,
        )
    }
}

@Composable
private fun DetailRow(label: String, value: String) {
    val spacing = GrokgenTheme.spacing
    val colors = MaterialTheme.colorScheme
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = spacing.md),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.Top,
    ) {
        Text(label, style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
        Spacer(Modifier.size(spacing.lg))
        Text(
            text = value,
            style = MaterialTheme.typography.bodySmall,
            color = colors.onBackground,
            textAlign = TextAlign.End,
        )
    }
}

/**
 * 每一档都要说清**该做什么**，不只是说失败了。
 *
 * 传输层那几档直接复用连接页的文案（`ui/component/ConnectionAdvice.kt`）。
 */
private fun describe(outcome: UploadOutcome): Pair<String, String?> = when (outcome) {
    is UploadOutcome.Uploaded -> "上传成功" to null

    is UploadOutcome.Unreachable -> adviceFor(outcome.reason).let { it.title to it.detail }

    UploadOutcome.UnsupportedFormat ->
        "服务器不认这个格式" to
            "⚠️ 正常情况下走不到这里 —— App 只会发 JPEG、PNG、WebP。" +
            "看到这条说明转码那一步被绕过了，请把它报给开发者。"

    is UploadOutcome.TooLarge ->
        "这张图太大" to (outcome.detail ?: "超过服务器 32 MB 的上限。换一张，或先在相册里导出一份小的。")

    UploadOutcome.EmptyBody ->
        "这张图读出来是 0 字节" to
            "如果它是云相册里还没下载到本机的照片，先在相册里打开它等它下载完，再回来选。"

    is UploadOutcome.ComfyUnreachable ->
        "网关在线，但连不上 ComfyUI" to
            ((outcome.detail ?: "") + "\n图是经 ComfyUI 落盘的，先把它起起来再传。").trim()

    is UploadOutcome.NotAnImage ->
        "服务器读不出这张图的尺寸" to
            ((outcome.detail ?: "") + "\n换一张试试；每一张都这样的话，到服务器上确认 ffprobe 还在（连接页会显示这一项）。").trim()

    is UploadOutcome.MalformedRequest ->
        "请求形状不对" to "这是 App 的 bug，不是你操作错了。${outcome.detail.orEmpty()}"

    is UploadOutcome.HttpError ->
        "服务器回了 HTTP ${outcome.code}" to outcome.detail

    UploadOutcome.SourceUnreadable ->
        "这张图读不到了" to "可能在上传过程中被删了，或者相册的授权失效了。回去重新选一张。"

    is UploadOutcome.TranscodeFailed ->
        "转码失败" to outcome.reason

    UploadOutcome.TooBigToDecode ->
        "这张图太大，手机上解不开" to "换一张，或者先用相册导出一份小一点的。"

    is UploadOutcome.Unexpected ->
        "没见过的失败" to outcome.throwable.toString()
}

private fun formatBytes(bytes: Long): String = when {
    bytes < 0 -> "未知"
    bytes < 1024 -> "$bytes B"
    bytes < 1024 * 1024 -> String.format("%.1f KB", bytes / 1024.0)
    else -> String.format("%.1f MB", bytes / 1024.0 / 1024.0)
}
