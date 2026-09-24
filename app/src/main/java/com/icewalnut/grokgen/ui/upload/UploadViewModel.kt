package com.icewalnut.grokgen.ui.upload

import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.icewalnut.grokgen.AppContainer
import com.icewalnut.grokgen.data.LocalImageFacts
import com.icewalnut.grokgen.net.ServerAddress
import com.icewalnut.grokgen.net.UploadOutcome
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/** 上传这件事走到哪一步了。 */
sealed interface UploadPhase {
    data object Idle : UploadPhase

    /**
     * 正在解码 + 重编码。
     *
     * ⚠️ **这一步要单独显示，不能和上传混成一个进度条。**
     * 一张 12 MP 的 HEIC 解码要好几秒，混在一起用户会以为卡住了 ——
     * 这和 `loading_model` 必须和 `sampling` 分开显示是同一个道理。
     */
    data object Transcoding : UploadPhase

    /** 正在传。[total] 为 `-1` 表示长度未知（分块传输），那时没有百分比。 */
    data class Uploading(val sent: Long, val total: Long) : UploadPhase

    data object Finished : UploadPhase
}

data class UploadUiState(
    val pickedUri: Uri? = null,
    val localFacts: LocalImageFacts? = null,
    val phase: UploadPhase = UploadPhase.Idle,
    val outcome: UploadOutcome? = null,
)

class UploadViewModel(private val container: AppContainer) : ViewModel() {

    private val _state = MutableStateFlow(UploadUiState())
    val state: StateFlow<UploadUiState> = _state.asStateFlow()

    /** 用户从相册选了一张。先只读文件头把尺寸显示出来，**不解码全图**。 */
    fun onPicked(uri: Uri) {
        _state.value = UploadUiState(pickedUri = uri)
        viewModelScope.launch {
            val facts = container.uploadRepository.readLocalFacts(uri)
            _state.value = _state.value.copy(localFacts = facts)
        }
    }

    fun upload() {
        val uri = _state.value.pickedUri ?: return
        if (_state.value.phase is UploadPhase.Uploading) return

        val facts = _state.value.localFacts
        // 要转码的话先显示「正在转码」；直传的就直接进上传态。
        val startPhase = when {
            facts == null -> UploadPhase.Transcoding
            facts.plan is com.icewalnut.grokgen.data.UploadPlan.TranscodeToJpeg -> UploadPhase.Transcoding
            else -> UploadPhase.Uploading(0, facts.sizeBytes)
        }
        _state.value = _state.value.copy(phase = startPhase, outcome = null)

        viewModelScope.launch {
            val saved = container.settingsStore.serverInput.first()
            val baseUrl = ServerAddress.toBaseUrl(saved)
            if (baseUrl == null) {
                _state.value = _state.value.copy(
                    phase = UploadPhase.Finished,
                    outcome = UploadOutcome.TranscodeFailed("后端地址是空的，先回连接页填一个"),
                )
                return@launch
            }

            // ⚠️ 节流：每次 socket 写都更新状态会把 Compose 重组压垮。
            //    64 KiB 一档 —— 12 MB 大约 190 次更新，界面上看着是连续的。
            var lastReported = 0L
            val outcome = container.uploadRepository.upload(baseUrl, uri) { sent, total ->
                if (sent - lastReported >= PROGRESS_STEP_BYTES || sent == total) {
                    lastReported = sent
                    _state.value = _state.value.copy(phase = UploadPhase.Uploading(sent, total))
                }
            }

            _state.value = _state.value.copy(phase = UploadPhase.Finished, outcome = outcome)
        }
    }

    fun reset() {
        _state.value = UploadUiState()
    }

    class Factory(private val container: AppContainer) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            UploadViewModel(container) as T
    }

    private companion object {
        const val PROGRESS_STEP_BYTES = 64L * 1024
    }
}
