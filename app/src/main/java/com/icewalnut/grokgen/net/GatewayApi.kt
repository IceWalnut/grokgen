package com.icewalnut.grokgen.net

import com.icewalnut.grokgen.net.dto.HealthDto
import com.icewalnut.grokgen.net.dto.UploadedImageDto
import okhttp3.MultipartBody
import retrofit2.Response
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.Part

/**
 * 网关的 HTTP 接口，一比一对应 `Docs/contract/gateway_api_v0.1.md`。
 *
 * ⚠️ 契约改了，先改契约文档，再改这里，再改用它的地方 —— 顺序不能反。
 *
 * M2R1 只接一个接口。上传是 M2R2，任务那五个是 M2R3/R4。
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
}
