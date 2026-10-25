package com.book2course.learner.data.api

import kotlinx.serialization.SerializationException
import retrofit2.HttpException
import java.io.IOException

/** Failures the UI can explain to a learner (messages live in strings.xml, see ui/ErrorText.kt). */
sealed interface AppError {
    data object NotConfigured : AppError
    data object Unauthorized : AppError
    data object NotFound : AppError
    data object ServerUnavailable : AppError
    data object Offline : AppError
    data object Incompatible : AppError
    data class Unexpected(val code: Int?) : AppError
}

fun httpError(code: Int): AppError = when (code) {
    401, 403 -> AppError.Unauthorized
    404 -> AppError.NotFound
    422 -> AppError.Incompatible
    502, 503, 504 -> AppError.ServerUnavailable
    else -> AppError.Unexpected(code)
}

fun Throwable.toAppError(): AppError = when (this) {
    is AppErrorException -> error
    is HttpException -> httpError(code())
    is SerializationException, is IllegalArgumentException -> AppError.Incompatible
    is IOException -> AppError.Offline
    else -> AppError.Unexpected(null)
}

class AppErrorException(val error: AppError) : Exception(error.toString())
