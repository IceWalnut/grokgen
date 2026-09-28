package com.icewalnut.grokgen.ui.queue

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.icewalnut.grokgen.AppContainer
import com.icewalnut.grokgen.data.JobPoller
import com.icewalnut.grokgen.model.isTerminalState
import com.icewalnut.grokgen.net.JobClient
import com.icewalnut.grokgen.net.JobListResult
import com.icewalnut.grokgen.net.JobQueryOutcome
import com.icewalnut.grokgen.net.ServerAddress
import com.icewalnut.grokgen.net.dto.JobDto
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.emitAll
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onStart
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update

/**
 * @param jobs 上一次**成功**拿到的列表；还没成功过则 `null`。
 * @param total 网关报的总条数（截断前）。大于 `jobs.size` 时列表被截掉了。
 * @param trouble 最近一次查询**没成功**的原因；成功后清空。
 *   ⚠️ 有它时界面显示「连接中断，重试中」横条，**列表保留上一次的内容** —— 不是任务出了问题。
 */
data class QueueUiState(
    val jobs: List<JobDto>? = null,
    val total: Int = 0,
    val trouble: JobQueryOutcome<Nothing>? = null,
)

/**
 * 队列页。
 *
 * ⭐ **轮询跟着界面的收集走**：[state] 用 `stateIn(WhileSubscribed(0))` 共享，
 * 界面用 `collectAsStateWithLifecycle` 收集 —— App 退到后台，收集取消，
 * 上游的轮询随之停止；回到前台，重新开始一轮（先立刻查一次，因为这期间状态可能变了好几次）。
 *
 * ⚠️ `WhileSubscribed(0)` 而不是常见的 5 秒宽限：VS-36 要求退到后台就停，
 * 宽限期内会多发一两个请求。代价是转屏时会立刻重新查一次 —— 那是一个很便宜的请求。
 */
@OptIn(ExperimentalCoroutinesApi::class)
class QueueViewModel(
    private val container: AppContainer,
    private val poller: JobPoller = JobPoller(),
) : ViewModel() {

    /** 累积的界面状态。轮询重启时**不清空** —— 回到前台时先显示上一次的内容，而不是一片空白。 */
    private val accumulated = MutableStateFlow(QueueUiState())

    /** 每次加一就重启一轮轮询（「刷新」按钮用）。 */
    private val refreshes = MutableStateFlow(0)

    private val driver: Flow<Unit> = refreshes
        .flatMapLatest { pollList() }
        .map { outcome -> accumulated.update { reduce(it, outcome) } }

    val state: StateFlow<QueueUiState> =
        combine(accumulated, driver.onStart { emit(Unit) }) { current, _ -> current }
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(0), QueueUiState())

    /**
     * 手动刷新。
     *
     * 列表全部到终态后轮询会自己停下（没有要等的了）；之后在生成页又提交了任务，
     * 回到这一页会自动重新开始 —— 这个按钮是给「就想现在再看一眼」用的。
     */
    fun refresh() {
        refreshes.update { it + 1 }
    }

    private fun pollList(): Flow<JobListResult> = flow {
        val baseUrl = ServerAddress.toBaseUrl(container.settingsStore.serverInput.first())
        if (baseUrl == null) {
            emit(JobQueryOutcome.HttpError(0, "后端地址是空的，先回连接页填一个"))
            return@flow
        }
        val api = container.gatewayApi(baseUrl)
        emitAll(
            poller.poll(
                fetch = { JobClient.listJobs(api) },
                // 全部到终态（包括空列表）就没有要等的了。
                isFinished = { list -> list.items.all { isTerminalState(it.state) } },
            ),
        )
    }

    private fun reduce(current: QueueUiState, outcome: JobListResult): QueueUiState = when (outcome) {
        is JobQueryOutcome.Found -> QueueUiState(jobs = outcome.value.items, total = outcome.value.total, trouble = null)
        JobQueryOutcome.NotFound -> current.copy(trouble = JobQueryOutcome.NotFound)
        is JobQueryOutcome.Unreachable -> current.copy(trouble = outcome)
        is JobQueryOutcome.HttpError -> current.copy(trouble = outcome)
    }

    class Factory(private val container: AppContainer) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T = QueueViewModel(container) as T
    }
}
