package com.icewalnut.grokgen.ui.queue

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.icewalnut.grokgen.AppContainer
import com.icewalnut.grokgen.data.JobPoller
import com.icewalnut.grokgen.model.isTerminalState
import com.icewalnut.grokgen.net.CancelOutcome
import com.icewalnut.grokgen.net.GatewayApi
import com.icewalnut.grokgen.net.JobClient
import com.icewalnut.grokgen.net.JobQueryOutcome
import com.icewalnut.grokgen.net.JobResult
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
import kotlinx.coroutines.launch

/** 取消这件事走到哪一步了。 */
sealed interface CancelPhase {
    data object Idle : CancelPhase

    /**
     * 点了一次，等第二次确认。
     *
     * ⚠️ 两步确认是故意的：运行中的任务一旦取消，已经付出的 GPU 时间就白费了，
     * 一次误触的代价是再等一分多钟。
     */
    data object Confirming : CancelPhase

    data object Sending : CancelPhase
    data class Finished(val outcome: CancelOutcome) : CancelPhase
}

/**
 * @param job 上一次**成功**拿到的任务；还没成功过则 `null`。
 * @param trouble 最近一次查询没成功的原因（「连接中断，重试中」）；成功后清空。
 * @param gone 网关里已经没有这个任务了（404，多半是网关重启过）。轮询已停。
 * @param cancelRequested 网关已经接受取消，但任务**还没到终态** —— 显示「正在取消…」。
 */
data class JobDetailUiState(
    val job: JobDto? = null,
    val trouble: JobQueryOutcome<Nothing>? = null,
    val gone: Boolean = false,
    val cancel: CancelPhase = CancelPhase.Idle,
    val cancelRequested: Boolean = false,
)

/**
 * 任务详情。轮询方式与 [QueueViewModel] 相同：跟着界面的收集走，任务到终态或查到 404 就停。
 */
@OptIn(ExperimentalCoroutinesApi::class)
class JobDetailViewModel(
    private val container: AppContainer,
    val jobId: String,
    private val poller: JobPoller = JobPoller(),
) : ViewModel() {

    private val accumulated = MutableStateFlow(JobDetailUiState())
    private val refreshes = MutableStateFlow(0)

    private val driver: Flow<Unit> = refreshes
        .flatMapLatest { pollJob() }
        .map { outcome -> accumulated.update { reduce(it, outcome) } }

    val state: StateFlow<JobDetailUiState> =
        combine(accumulated, driver.onStart { emit(Unit) }) { current, _ -> current }
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(0), JobDetailUiState())

    /** 第一次点：进入确认；第二次点：发出去。 */
    fun onCancelClicked() {
        when (accumulated.value.cancel) {
            CancelPhase.Idle, is CancelPhase.Finished -> accumulated.update { it.copy(cancel = CancelPhase.Confirming) }
            CancelPhase.Confirming -> sendCancel()
            CancelPhase.Sending -> Unit // 连点防护
        }
    }

    fun dismissCancelConfirm() {
        if (accumulated.value.cancel == CancelPhase.Confirming) {
            accumulated.update { it.copy(cancel = CancelPhase.Idle) }
        }
    }

    private fun sendCancel() {
        accumulated.update { it.copy(cancel = CancelPhase.Sending) }
        viewModelScope.launch {
            val api = api()
            val outcome = if (api == null) {
                CancelOutcome.HttpError(0, "后端地址是空的，先回连接页填一个")
            } else {
                JobClient.cancel(api, jobId)
            }
            accumulated.update { current ->
                when (outcome) {
                    // ⚠️ 200 不等于取消完了：运行中的任务是异步中断的。
                    //    先用返回的状态更新，标记「正在取消」，轮询会一路看到终态。
                    is CancelOutcome.Accepted -> current.copy(
                        job = outcome.job,
                        cancel = CancelPhase.Finished(outcome),
                        cancelRequested = !isTerminalState(outcome.job.state),
                    )
                    else -> current.copy(cancel = CancelPhase.Finished(outcome))
                }
            }
            // 409：任务已经结束了。重启一轮轮询，立刻把真实的终态拿回来。
            // 404：同样重启一次，轮询会拿到 NotFound 并显示「网关里已经没有这个任务」。
            if (outcome is CancelOutcome.AlreadyFinished || outcome == CancelOutcome.NotFound) {
                refreshes.update { it + 1 }
            }
        }
    }

    private suspend fun api(): GatewayApi? =
        ServerAddress.toBaseUrl(container.settingsStore.serverInput.first())?.let { container.gatewayApi(it) }

    private fun pollJob(): Flow<JobResult> = flow {
        val api = api()
        if (api == null) {
            emit(JobQueryOutcome.HttpError(0, "后端地址是空的，先回连接页填一个"))
            return@flow
        }
        emitAll(poller.poll(fetch = { JobClient.getJob(api, jobId) }, isFinished = { isTerminalState(it.state) }))
    }

    private fun reduce(current: JobDetailUiState, outcome: JobResult): JobDetailUiState = when (outcome) {
        is JobQueryOutcome.Found -> current.copy(
            job = outcome.value,
            trouble = null,
            gone = false,
            cancelRequested = current.cancelRequested && !isTerminalState(outcome.value.state),
        )
        JobQueryOutcome.NotFound -> current.copy(gone = true, trouble = null)
        is JobQueryOutcome.Unreachable -> current.copy(trouble = outcome)
        is JobQueryOutcome.HttpError -> current.copy(trouble = outcome)
    }

    class Factory(private val container: AppContainer, private val jobId: String) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T = JobDetailViewModel(container, jobId) as T
    }
}
