import re

from rest_framework.views import exception_handler


RU_ERROR_TRANSLATIONS = {
    "Authentication credentials were not provided.": "Необходимо войти в приложение.",
    "Invalid username/password.": "Неверный логин или пароль.",
    "Invalid basic header. No credentials provided.": "Не удалось проверить данные входа.",
    "You do not have permission to perform this action.": "Недостаточно прав для выполнения этого действия.",
    "Not found.": "Объект не найден.",
    "Unknown kind.": "Неизвестный тип данных.",
    "This field is required.": "Заполните обязательное поле.",
    "This field may not be null.": "Обязательное поле не может быть пустым.",
    "This field may not be blank.": "Заполните обязательное поле.",
    "A valid integer is required.": "Укажите целое число.",
    "A valid number is required.": "Укажите корректное число.",
    "Enter a valid email address.": "Укажите корректный адрес электронной почты.",
    "Date has wrong format. Use one of these formats instead: YYYY-MM-DD.": "Укажите дату в формате ГГГГ-ММ-ДД.",
    "Time has wrong format. Use one of these formats instead: hh:mm[:ss[.uuuuuu]].": "Укажите корректное время.",
    "Datos de reserva inválidos.": "Проверьте данные записи.",
    "Sin permiso para gestionar caja.": "Нет разрешения на управление кассой.",
    "Sin permiso para crear clientes.": "Нет разрешения на создание клиентов.",
    "Sin permiso para eliminar clientes.": "Нет разрешения на удаление клиентов.",
    "Sin permiso para eliminar empleados.": "Нет разрешения на удаление работников.",
    "Sin permiso para eliminar servicios.": "Нет разрешения на удаление услуг.",
    "Sin permiso para eliminar zonas.": "Нет разрешения на удаление зон.",
    "Sin permiso para crear empleados.": "Нет разрешения на создание работников.",
    "Sin permiso para editar este empleado.": "Нет разрешения на изменение этого работника.",
    "Solo puedes editar tus datos de contacto, color y servicios.": "Можно изменять только свои контактные данные, цвет и услуги.",
    "Sin permiso para editar premios.": "Нет разрешения на изменение бонусов.",
    "Sin permiso para editar servicios.": "Нет разрешения на изменение услуг.",
    "Sin permiso para editar zonas.": "Нет разрешения на изменение зон.",
    "Sin acceso a este cliente.": "Нет доступа к этому клиенту.",
    "Sin acceso a este empleado.": "Нет доступа к этому работнику.",
    "Sin acceso a esta reserva.": "Нет доступа к этой записи.",
    "Sin acceso a este documento.": "Нет доступа к этому документу.",
    "Sin acceso a este pago.": "Нет доступа к этому платежу.",
    "Sin acceso a esta foto.": "Нет доступа к этой фотографии.",
    "Sin acceso a este bloqueo.": "Нет доступа к этой паузе.",
    "Solo administracion puede cambiar la visibilidad de fotos.": "Только администратор может менять видимость фотографий.",
    "Esta foto no esta visible.": "Эта фотография скрыта.",
    "Fecha inválida.": "Укажите корректную дату.",
    "Fecha inválida. Usa el formato YYYY-MM-DD.": "Укажите дату в формате ГГГГ-ММ-ДД.",
    "Formato esperado YYYY-MM-DD.": "Укажите дату в формате ГГГГ-ММ-ДД.",
    "La fecha final no puede ser anterior a la inicial.": "Конечная дата не может быть раньше начальной.",
    "La fecha final debe ser posterior o igual a la inicial.": "Конечная дата должна совпадать с начальной или быть позже неё.",
    "La fecha inicial no puede ser posterior a la final.": "Начальная дата не может быть позже конечной.",
    "El rango máximo de caja es de 367 días.": "Диапазон кассы не может превышать 367 дней.",
    "Este usuario ya existe.": "Пользователь с такими данными уже существует.",
    "Este usuario ya esta vinculado a otro empleado.": "Этот пользователь уже привязан к другому работнику.",
    "Introduce una contraseña inicial.": "Укажите первоначальный пароль.",
    "Introduce un usuario para crear el acceso.": "Укажите логин для создания доступа.",
    "Introduce la contraseña actual.": "Введите текущий пароль.",
    "La contraseña actual no es correcta.": "Текущий пароль указан неверно.",
    "Tu usuario no tiene empleado vinculado.": "К вашей учётной записи не привязан работник.",
    "Este empleado no realiza el servicio seleccionado.": "Выбранный работник не выполняет эту услугу.",
    "Selecciona al menos una zona para este servicio.": "Выберите хотя бы одну зону для этой услуги.",
    "La zona seleccionada no está permitida para este servicio.": "Выбранная зона недоступна для этой услуги.",
    "Este empleado no trabaja en la zona seleccionada.": "Выбранный работник не работает в этой зоне.",
    "Ese horario no está disponible para el empleado o la zona.": "Это время недоступно для выбранного работника или зоны.",
    "No hay zona libre para este horario.": "На это время нет свободной зоны.",
    "Esta reserva no se puede cancelar.": "Эту запись нельзя отменить.",
    "Este cliente no puede crear reservas online.": "Этот клиент не может создавать онлайн-записи.",
    "No se puede cambiar la cita con menos de 24 horas de antelación. Contacta con el salón.": "Запись нельзя изменить менее чем за 24 часа. Свяжитесь с салоном.",
    "El horario seleccionado no está disponible.": "Выбранное время недоступно.",
    "No hay disponibilidad para la nueva duración del servicio.": "Нет свободного времени для новой продолжительности услуги.",
    "La reserva debe empezar y terminar el mismo día.": "Запись должна начинаться и заканчиваться в один день.",
    "El empleado ya tiene una reserva en ese horario.": "У работника уже есть запись на это время.",
    "La zona ya está ocupada en ese horario.": "Зона уже занята в это время.",
    "Importe inválido.": "Укажите корректную сумму.",
    "Importe no válido.": "Укажите корректную сумму.",
    "El importe debe ser mayor que cero.": "Сумма должна быть больше нуля.",
    "El importe no puede superar el precio de la reserva.": "Сумма не может превышать стоимость записи.",
    "Método de pago inválido.": "Выберите корректный способ оплаты.",
    "Método de pago no válido.": "Выберите корректный способ оплаты.",
    "Esta reserva no se puede pagar online.": "Эту запись нельзя оплатить онлайн.",
    "Esta reserva ya está pagada.": "Эта запись уже оплачена.",
    "Stripe no está configurado.": "Stripe ещё не настроен.",
    "Solo están habilitadas las retiradas en EUR.": "Вывод средств доступен только в евро.",
    "Método de retirada no válido.": "Выберите корректный способ вывода средств.",
    "Solicitud de retirada no válida.": "Запрос на вывод средств недействителен.",
    "Contraseña incorrecta.": "Неверный пароль.",
    "Introduce un porcentaje valido.": "Укажите корректный процент.",
    "El porcentaje debe estar entre 0 y 100.": "Процент должен быть от 0 до 100.",
    "Introduce importes válidos para el prepago.": "Укажите корректные значения предоплаты.",
    "El mínimo no puede ser negativo.": "Минимальная сумма не может быть отрицательной.",
    "Redondeo no válido.": "Выберите корректный вариант округления.",
    "Solicitud no válida.": "Запрос недействителен.",
    "Destino no autorizado en Stripe.": "Stripe не разрешает вывод на выбранный счёт.",
    "Tipo de documento no válido.": "Выберите корректный тип документа.",
    "La caja de esa fecha ya está cerrada.": "Касса за эту дату уже закрыта.",
    "La caja de hoy ya está cerrada.": "Касса за сегодня уже закрыта.",
    "El documento ya está totalmente cobrado.": "Документ уже полностью оплачен.",
    "El pago supera el saldo pendiente del documento.": "Платёж превышает остаток по документу.",
    "La devolución supera lo ya cobrado en el documento.": "Возврат превышает оплаченную сумму документа.",
    "El documento aun no esta cobrado completo.": "Документ ещё не оплачен полностью.",
    "El cliente no tiene email.": "У клиента не указан адрес электронной почты.",
    "El cliente no tiene telefono.": "У клиента не указан телефон.",
    "Indica un concepto.": "Укажите наименование позиции.",
    "Indica un importe.": "Укажите сумму.",
    "No se puede convertir un bloqueo puntual en recurrente.": "Разовую паузу нельзя преобразовать в повторяющуюся.",
    "Este campo es obligatorio.": "Заполните обязательное поле.",
    "La hora de fin debe ser posterior al inicio.": "Время окончания должно быть позже времени начала.",
    "El bloqueo debe empezar y terminar el mismo día.": "Пауза должна начинаться и заканчиваться в один день.",
    "El bloqueo se solapa con otro bloqueo del empleado.": "Пауза пересекается с другой паузой работника.",
    "El bloqueo se solapa con una reserva existente.": "Пауза пересекается с существующей записью.",
    "El bloqueo recurrente se solapa con otro bloqueo del empleado.": "Повторяющаяся пауза пересекается с другой паузой работника.",
    "Estado invalido.": "Выберите корректный статус.",
    "Introduce un numero entero.": "Укажите целое число.",
    "Debe estar entre 0 y 10080 minutos.": "Значение должно быть от 0 до 10080 минут.",
}


def _collect_messages(value):
    if isinstance(value, dict):
        result = []
        for child in value.values():
            result.extend(_collect_messages(child))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for child in value:
            result.extend(_collect_messages(child))
        return result
    if value is None:
        return []
    return [str(value)]


def _translate_dynamic(message):
    if re.fullmatch(r"[^ ]+ ya está totalmente cobrado\.", message):
        number = message.split(" ", 1)[0]
        return f"Документ {number} уже полностью оплачен."
    if message.startswith("Saldo disponible "):
        amount = message.removeprefix("Saldo disponible ")
        return f"Доступно для вывода: {amount}"
    if any("а" <= char.lower() <= "я" or char.lower() == "ё" for char in message):
        return message
    return RU_ERROR_TRANSLATIONS.get(
        message,
        "Не удалось выполнить действие. Проверьте введённые данные и повторите попытку.",
    )


def mobile_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    request = context.get("request")
    language = request.headers.get("Accept-Language", "") if request else ""
    if not language.lower().startswith("ru"):
        return response

    translated = []
    for message in _collect_messages(response.data):
        value = _translate_dynamic(message)
        if value not in translated:
            translated.append(value)
    response.data = {"detail": translated or ["Не удалось выполнить действие."]}
    return response
