package com.icewalnut.grokgen.ui.component

import android.content.Context
import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.net.Uri
import android.os.Build
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * 显示一个 `content://` 图片的缩略图。
 *
 * ⚠️ **故意不引 Coil 这类图片加载库。** 为显示一张缩略图引一个依赖，
 * 不满足「任务确实需要」（`~/.claude/rules/workflow.md` 的 Guardrails）。
 * `ImageDecoder` 自带按目标尺寸采样，够用。
 *
 * ⚠️ 这张缩略图是**唯一一个活过单次函数调用的 bitmap**，
 * 所以它必须是**采样过的小图**（≤ [MAX_EDGE_PX]，约 1 MB），
 * 而且**绝不能和转码时那个几十 MB 的全尺寸 bitmap 同时存在**。
 */
@Composable
fun UriThumbnail(
    uri: Uri,
    contentScale: ContentScale,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    var bitmap by remember(uri) { mutableStateOf<ImageBitmap?>(null) }

    LaunchedEffect(uri) {
        bitmap = withContext(Dispatchers.IO) { decodeThumbnail(context, uri)?.asImageBitmap() }
    }

    val current = bitmap
    if (current == null) {
        // 解码中：留一个空盒子占位，不要闪一下别的颜色。
        Box(modifier = modifier.fillMaxSize())
    } else {
        Image(
            bitmap = current,
            contentDescription = "选中的图片",
            contentScale = contentScale,
            modifier = modifier.fillMaxSize(),
        )
    }
}

private const val MAX_EDGE_PX = 1024

private fun decodeThumbnail(context: Context, uri: Uri): Bitmap? {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return null
    return runCatching {
        val source = ImageDecoder.createSource(context.contentResolver, uri)
        ImageDecoder.decodeBitmap(source) { decoder, info, _ ->
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            decoder.isMutableRequired = false
            val longest = maxOf(info.size.width, info.size.height)
            // setTargetSampleSize 是 2 的幂；取一个不小于所需比例的幂。
            var sample = 1
            while (longest / sample > MAX_EDGE_PX) sample *= 2
            decoder.setTargetSampleSize(sample)
        }
    }.getOrNull()
}
