package com.icewalnut.grokgen.ui.generate

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.icewalnut.grokgen.AppContainer
import com.icewalnut.grokgen.model.FirstFrameImage
import com.icewalnut.grokgen.model.GenerateForm
import com.icewalnut.grokgen.model.toSubmitRequest
import com.icewalnut.grokgen.model.validate
import com.icewalnut.grokgen.net.JobSubmitter
import com.icewalnut.grokgen.net.ServerAddress
import com.icewalnut.grokgen.net.SubmitOutcome
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/** 提交这件事走到哪一步了。 */
sealed interface SubmitPhase {
    data object Idle : SubmitPhase
    data object Submitting : SubmitPhase

    /**
     * @param turbo 提交时选的档位。结果卡片要用它标注采样参数属于哪一档 ——
     *   不能读表单里的当前值，用户可能在结果出来后又拨了开关。
     */
    data class Finished(val outcome: SubmitOutcome, val turbo: Boolean) : SubmitPhase
}

data class GenerateUiState(
    val form: GenerateForm = GenerateForm(),
    val phase: SubmitPhase = SubmitPhase.Idle,
)

/**
 * 生成页。
 *
 * ⚠️ 这个 ViewModel 挂在 Activity 上（`MainActivity` 里用 `viewModel()` 取，没有导航库），
 * 所以跳去上传页再回来，表单还在。
 */
class GenerateViewModel(private val container: AppContainer) : ViewModel() {

    private val _state = MutableStateFlow(GenerateUiState())
    val state: StateFlow<GenerateUiState> = _state.asStateFlow()

    /**
     * 改表单。
     *
     * ⚠️ 表单一改就清掉上一次的**结果**（提交中除外）——
     * 否则「网关实际使用的参数」会挂在一张已经不是那次提交的表单下面，
     * 用户会把旧的 seed 当成新表单的。
     */
    fun updateForm(transform: (GenerateForm) -> GenerateForm) {
        val current = _state.value
        val nextPhase = if (current.phase is SubmitPhase.Submitting) current.phase else SubmitPhase.Idle
        _state.value = current.copy(form = transform(current.form), phase = nextPhase)
    }

    fun setFirstFrame(image: FirstFrameImage) = updateForm { it.copy(firstFrame = image) }

    fun submit() {
        val current = _state.value
        // ⚠️ 连点防护：提交中再点一次直接忽略。重复提交 = 多一个任务、多付一次 GPU。
        if (current.phase is SubmitPhase.Submitting) return
        // 界面上按钮已经禁用了，这里再挡一次 —— 不让一个非法表单有机会发出去。
        if (validate(current.form) != null) return

        val request = toSubmitRequest(current.form)
        val turbo = current.form.turbo
        _state.value = current.copy(phase = SubmitPhase.Submitting)

        viewModelScope.launch {
            val saved = container.settingsStore.serverInput.first()
            val baseUrl = ServerAddress.toBaseUrl(saved)
            val outcome = if (baseUrl == null) {
                // 地址是空的：请求肯定没发出去。按「没见过的失败」如实报，不伪装成连不上。
                SubmitOutcome.Unexpected(IllegalStateException("后端地址是空的，先回连接页填一个"))
            } else {
                JobSubmitter.submit(container.gatewayApi(baseUrl), request)
            }
            _state.value = _state.value.copy(phase = SubmitPhase.Finished(outcome, turbo))
        }
    }

    class Factory(private val container: AppContainer) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            GenerateViewModel(container) as T
    }
}
