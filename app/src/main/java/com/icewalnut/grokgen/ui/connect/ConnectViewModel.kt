package com.icewalnut.grokgen.ui.connect

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.icewalnut.grokgen.AppContainer
import com.icewalnut.grokgen.net.ConnectionOutcome
import com.icewalnut.grokgen.net.ServerAddress
import com.icewalnut.grokgen.net.classifyHealth
import com.icewalnut.grokgen.net.classifyNetworkFailure
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/** 连接页当前的样子。 */
data class ConnectUiState(
    /** 输入框里的内容，**原样**是用户写的那串。 */
    val input: String = "",
    val checking: Boolean = false,
    /** 还没连过时是 null。 */
    val outcome: ConnectionOutcome? = null,
    /** 输入本身就不合法（比如空白），连都不用连。 */
    val inputError: String? = null,
)

class ConnectViewModel(private val container: AppContainer) : ViewModel() {

    private val _state = MutableStateFlow(ConnectUiState())
    val state: StateFlow<ConnectUiState> = _state.asStateFlow()

    init {
        viewModelScope.launch {
            val saved = container.settingsStore.serverInput.first()
            _state.value = _state.value.copy(input = saved)
        }
    }

    fun onInputChange(value: String) {
        _state.value = _state.value.copy(input = value, inputError = null)
    }

    fun connect() {
        val current = _state.value
        if (current.checking) return

        val baseUrl = ServerAddress.toBaseUrl(current.input)
        if (baseUrl == null) {
            _state.value = current.copy(inputError = "地址不能为空")
            return
        }

        _state.value = current.copy(checking = true, outcome = null, inputError = null)

        viewModelScope.launch {
            container.settingsStore.setServerInput(current.input)

            val outcome = try {
                val response = container.gatewayApi(baseUrl).health()
                val body = response.body()
                when {
                    // ⭐ 这里是本页最容易写错的一步：HTTP 200 **不等于**一切正常。
                    //    网关即使在 ComfyUI 挂掉时也返回 200，用响应体告知 ——
                    //    那是它故意的设计。所以拿到 200 还要再看一眼 body。
                    response.isSuccessful && body != null -> classifyHealth(body)
                    response.isSuccessful -> ConnectionOutcome.HttpError(
                        code = response.code(),
                        body = "响应体是空的",
                    )
                    else -> ConnectionOutcome.HttpError(
                        code = response.code(),
                        body = response.errorBody()?.string(),
                    )
                }
            } catch (t: Throwable) {
                // ⚠️ 这里捕获的是「连不上」这一类可预期的失败，翻译成明确的结果给用户。
                //    分类不出来的会落进 Unexpected 并把原始异常带上，**不吞**。
                classifyNetworkFailure(t)
            }

            _state.value = _state.value.copy(checking = false, outcome = outcome)
        }
    }

    class Factory(private val container: AppContainer) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            ConnectViewModel(container) as T
    }
}
