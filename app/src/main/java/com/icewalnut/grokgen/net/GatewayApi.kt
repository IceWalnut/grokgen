package com.icewalnut.grokgen.net

import com.icewalnut.grokgen.net.dto.HealthDto
import com.icewalnut.grokgen.net.dto.JobDto
import com.icewalnut.grokgen.net.dto.JobListDto
import com.icewalnut.grokgen.net.dto.JobSubmitRequestDto
import com.icewalnut.grokgen.net.dto.JobSubmittedDto
import com.icewalnut.grokgen.net.dto.UploadedImageDto
import okhttp3.MultipartBody
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.Part
import retrofit2.http.Path
import retrofit2.http.Query

/**
 * 网关的 HTTP 接口，一比一对应 `Docs/contract/gateway_api_v0.1.md`。
 *
 * ⚠️ 契约改了，先改契约文档，再改这里，再改用它的地方 —— 顺序不能反。
 *
 * M2R1 接了健康检查，M2R2 接了上传，M2R3 接了提交任务，
 * M2R4 接了任务的查询、列表、取消；取回视频是 M2R5。
 */
interface GatewayApi {

    /**
     * 网关自身状态 + ComfyUI 是否可达。
     *
     * ⚠️ 返回 [Response] 而不是直接返回 [HealthDto]，是为了**能看到状态码**。
     * 契约里 409 和 404 是完全不同的意思（「再等等」对「不存在，别再问了」），
     * 这个区别从 M2R4 起是必需的 —— 现在就把形状定对，免得那时改一遍。
     */
    @GET("v1/health")
    suspend fun health(): Response<HealthDto>

    /**
     * 上传一张图，拿回一个 `asset_id`。
     *
     * ⚠️ multipart 的字段名**必须是 `file`** —— 网关那边是
     * `async def upload_image(request: Request, file: UploadFile)`，
     * FastAPI 从参数名推出字段名。名字不对是 422。
     *
     * ⚠️ 只有 filename 的**后缀**有意义：网关自己生成落盘文件名，
     * 且**完全不嗅探内容**，后缀是它唯一的闸门。
     */
    @Multipart
    @POST("v1/uploads/image")
    suspend fun uploadImage(@Part file: MultipartBody.Part): Response<UploadedImageDto>

    /**
     * 提交一个生成任务，立刻返回 `queued`（不等生成开始）。
     *
     * ⚠️ 请求体的每个键都必须写出来，留空的写 `null` —— 见 [JobSubmitRequestDto]。
     * ⚠️ 这个调用**有副作用**：重复调用 = 多一个任务。不要重试，见 [JobSubmitter]。
     */
    @POST("v1/jobs")
    suspend fun submitJob(@Body body: JobSubmitRequestDto): Response<JobSubmittedDto>

    /**
     * 任务列表，按创建时间倒序。
     *
     * ⚠️ **显式传 `limit`**，不依赖默认值 —— 契约与实现曾经对默认值说法不一（50 对 20）。
     */
    @GET("v1/jobs")
    suspend fun listJobs(@Query("limit") limit: Int): Response<JobListDto>

    /** 一个任务的完整状态。404 = 网关里没有它（网关重启会清空任务列表）。 */
    @GET("v1/jobs/{jobId}")
    suspend fun getJob(@Path("jobId") jobId: String): Response<JobDto>

    /**
     * 取消。返回处理后的任务。
     *
     * ⚠️ **200 不等于「已经取消完了」**：运行中的任务是异步中断的，返回时可能还是 `running`。
     * 调用方要继续查询直到终态。409 = 已结束或正在收尾；502 = 网关连不上 ComfyUI、中断没发出去。
     */
    @POST("v1/jobs/{jobId}/cancel")
    suspend fun cancelJob(@Path("jobId") jobId: String): Response<JobDto>
}
