"""Error titles and default details, keyed by HTTP status. User-facing text is Spanish."""

ERROR_TITLES = {
    400: "Petición inválida",
    401: "Sesión expirada",
    403: "Sin permiso",
    404: "No encontrado",
    405: "Método no permitido",
    409: "Conflicto",
    413: "Archivo demasiado grande",
    422: "Datos inválidos",
    429: "Demasiados intentos",
}
DEFAULT_ERROR_TITLE = "Error"
SERVER_ERROR_TITLE = "Error del servidor"

ERROR_DETAILS = {
    400: "La petición está mal formada.",
    401: "Tu sesión no es válida o expiró. Inicia sesión de nuevo.",
    403: "No tienes permiso para realizar esta acción.",
    404: "El recurso solicitado no existe.",
    405: "Ese método no está permitido en esta ruta.",
    409: "La operación entra en conflicto con el estado actual del recurso.",
    413: "El archivo supera el tamaño permitido.",
    422: "Los datos enviados no son válidos.",
    429: "Demasiados intentos. Espera unos minutos antes de volver a intentar.",
    500: "Ocurrió un error inesperado. Inténtalo de nuevo más tarde.",
}
SERVER_ERROR_DETAIL = ERROR_DETAILS[500]

# pydantic error type -> Spanish message for the field-level `errors` map.
VALIDATION_MESSAGES = {
    "missing": "Este campo es obligatorio",
    "string_too_short": "Este campo es demasiado corto",
    "string_too_long": "Este campo es demasiado largo",
    "string_type": "Debe ser texto",
    "int_parsing": "Debe ser un número entero",
    "int_type": "Debe ser un número entero",
    "bool_parsing": "Debe ser verdadero o falso",
    "bool_type": "Debe ser verdadero o falso",
    "uuid_parsing": "Identificador inválido",
    "uuid_type": "Identificador inválido",
    "greater_than_equal": "El valor es demasiado bajo",
    "less_than_equal": "El valor es demasiado alto",
    "extra_forbidden": "Este campo no se puede modificar",
    "list_type": "Debe ser una lista",
    "too_long": "La lista es demasiado larga",
    "value_error": "Valor inválido",
}
DEFAULT_VALIDATION_MESSAGE = "Valor inválido"
JSON_INVALID_TYPES = frozenset({"json_invalid", "model_attributes_type"})

EMAIL_MESSAGE = "Correo electrónico inválido"
