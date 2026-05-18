# BRIMOON Studio: plan de paridad web y mobile

Este documento marca que debe tener el panel web para quedar alineado con la app mobile. La idea no es rehacer todo de golpe, sino ir por bloques y dejar cada parte usable antes de pasar a la siguiente.

## Estado general

La app mobile ya tiene una version mas completa del producto: roles, calendario, clientes, empleados, servicios, zonas, premios, fotos, portal cliente y validaciones de reservas. El backend web ya tiene muchas piezas, pero algunas pantallas todavia muestran datos antiguos o no usan toda la logica nueva.

## 1. Clientes

Mobile ya tiene:

- ficha completa del cliente;
- avatar;
- datos de contacto interactivos;
- historial de reservas clicable;
- fotos visibles/no visibles para cliente;
- arbol de referidos;
- empleados habituales clicables;
- premios configurables;
- progreso por premios de amigos, visitas y gasto;
- acceso cliente con usuario/password.

Web debe quedar asi:

- mostrar los mismos premios que mobile, usando `ClientRewardRule`;
- dejar atras la logica fija de "cada 5 referidos";
- mostrar explicacion clara de cada premio;
- mostrar si el premio esta disponible, usado o cuanto falta;
- mantener historial, fotos y referidos;
- agregar gestion web de credenciales del cliente;
- agregar avatar en lista/detalle si no esta ya visible.

Primer bloque de trabajo:

- sincronizar premios de cliente en la ficha web con `client_reward_progress`.
- crear portal web cliente con perfil, avatar, premios, fotos visibles, historial y solicitud de reserva.
- usar endpoint de disponibilidad para que el cliente elija hora y master disponibles.

## 2. Reservas y calendario

Mobile ya tiene:

- crear reserva desde slot vacio;
- elegir entre reserva y pausa/bloqueo;
- drag-and-drop con validacion;
- editar reserva desde calendario;
- filtro por empleado persistente;
- validacion servicio/empleado/zona/horario;
- origen de reserva;
- fotos antes/despues;
- visibilidad de fotos para cliente.

Web debe quedar asi:

- usar las mismas validaciones antes de guardar;
- filtrar empleados por servicio y servicios por empleado;
- filtrar zonas por servicio;
- mostrar errores claros;
- mantener origen de reserva;
- permitir fotos y visibilidad igual que mobile;
- evitar que el calendario permita combinaciones imposibles.

## 3. Empleados

Mobile ya tiene:

- ficha completa del empleado;
- color del calendario;
- servicios que realiza;
- estadisticas de ingresos, clientes y reservas;
- usuario/password de empleado;
- modo empleado sin ver comision;
- edicion del propio perfil.

Web debe quedar asi:

- mantener analitica actual;
- revisar que la ficha web tenga el mismo detalle que mobile;
- separar lo que ve admin de lo que ve empleado;
- facilitar credenciales y cambio de password;
- no mostrar comision al empleado si no es admin.

## 4. Servicios, zonas y premios

Mobile ya tiene:

- servicios con precio, duracion, color, activo/inactivo;
- zonas con color y activo/inactivo;
- asignacion de zonas a servicios;
- premios configurables desde Salon;
- colores elegidos con selector visual.

Web debe quedar asi:

- conservar CRUD de servicios y zonas;
- mostrar selector visual de color si falta en algun formulario;
- agregar pantalla web clara de premios si solo existe en API/admin;
- mostrar que premios estan activos y que descuento aplican.

## 5. Portal cliente

Mobile ya tiene:

- login de cliente;
- resumen personal;
- historial;
- fotos autorizadas;
- premios y progreso;
- solicitud de reserva;
- activacion de premio al reservar.

Web debe quedar asi:

- decidir si el cliente tambien tendra portal web;
- si lo tendra, replicar las pantallas basicas: perfil, historial, fotos, premios y nueva reserva.

## Orden recomendado

1. Clientes y premios.
2. Formularios de reserva y calendario.
3. Credenciales cliente/empleado en web.
4. Servicios, zonas y premios como bloque de Salon.
5. Portal cliente web.
