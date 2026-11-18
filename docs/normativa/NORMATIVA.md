# Norma del proyecto aéreo

Estas cuatro gacetas son la norma con la que se diseña y se revisa el sistema.
El contrato de la API sigue diciendo cómo se comporta el software. Si una pantalla
nueva toca la operación, el vuelo, el mantenimiento, la tripulación, los pasajeros
o la plataforma, la regla sale de aquí.

Los PDF están en `docs/normativa/fuentes/`. Se leen en este orden.

## Orden

1. **RAV 119.** Certificación del explotador de servicio público y del servicio
   especializado de transporte aéreo. Providencia PRE-CJU-GDA-092-25, Caracas,
   13 de marzo de 2025.
   Archivo: `01-RAV-119-certificacion-explotador.pdf` (texto seleccionable, 66 páginas).
2. **RAV 135.** Requisitos de operación: operaciones nacionales e internacionales,
   regulares y no regulares. Providencia PRE-CJU-GDA-350-25, Caracas, 21 de
   octubre de 2025. Enmienda 49 del Anexo 6, Parte I.
   Archivo: `02-RAV-135-operaciones.pdf` (escaneo con reconocimiento de texto, 281 páginas).
3. **RAV 91.** Reglas de vuelo y operación general. Gaceta Oficial N° 6.906
   Extraordinario, Caracas, 16 de mayo de 2025.
   Archivo: `03-RAV-91-reglas-de-vuelo.pdf` (gaceta en imagen, sin capa de texto, 72 páginas).
4. **RAV 111.** Operaciones de plataforma, certificación y operación de los
   servicios especializados aeroportuarios. Providencia PRE-CJU-GDA-136-20,
   7 de agosto de 2020. Gaceta Oficial N° 41.959, 7 de septiembre de 2020.
   Archivo: `04-RAV-111-plataforma.pdf` (28 páginas).

## Qué manda en cada vuelo

- Avión de servicio público o especializado, 19 asientos de pasajeros o menos
  (sin la tripulación) y masa máxima de despegue de 5.700 kg o menos: se opera
  según la **RAV 135**. Lo dice la sección 119.10. El certificado (AOC) y las
  especificaciones operacionales salen de la **RAV 119**.
- Helicóptero de servicio especializado hasta 12 puestos, sin la tripulación:
  también **RAV 135**, sección 119.10 (b)(4).
- Avión de servicio público con más de 19 asientos o más de 5.700 kg: **RAV 121**,
  sección 119.9. Esa gaceta no está en esta carpeta.
- Operación que no es servicio público, con 20 asientos o más: **RAV 125**,
  sección 119.11. Esa gaceta no está en esta carpeta.
- La **RAV 91** es la regla general de vuelo. Se usa en lo que la 135 no regula.
- La **RAV 111** es la plataforma: rampa, equipos de tierra, combustible,
  estacionamiento, atención al pasajero y despacho en tierra.

Para operar hace falta AOC vigente, especificaciones operacionales del modelo
y el cumplimiento permanente de la RAV que corresponda (secciones 119.5, 119.13
y 119.24).

## RAV 119, por capítulo

- A. Generalidades: objeto, definiciones, AOC, prohibiciones, especificaciones operacionales.
- B. Qué operación cae en la 121, en la 135 o en la 125 (secciones 119.9 a 119.11).
- C. Certificación: cinco fases, AOC, enmienda, suspensión, contratos de aeronave,
  exención de emergencia, auditoría, y personal clave (sección 119.39).
- D y E. Condiciones del servicio público y del servicio especializado, regulares
  y no regulares, tarifas.
- F. Responsabilidad civil y registro de estadísticas del servicio especializado.

Personal clave de la sección 119.39, o su equivalente aceptado por el INAC:
Presidente, Gerente de operaciones, Gerente de mantenimiento, Gerente del SMS,
Gerente de seguridad de la aviación, Gerente de calidad, Gerente de servicios en
tierra, Jefe de instrucción, Jefe de pilotos, Jefe de flota por modelo y Jefe de
tripulación de cabina. Esos cargos van descritos en el manual de operaciones.

## RAV 135, por capítulo

- A. Generalidades y aplicabilidad. Exige certificado según la RAV 119.
- B. Operaciones de vuelo.
- C. Instrumentos y equipos.
- D. Limitaciones VFR/IFR e información meteorológica.
- E. Tripulación de vuelo.
- F. Gestión de la fatiga.
- G. Verificaciones de la tripulación.
- H. Programas de instrucción.
- I. Limitaciones de performance.
- J. Control y requisitos de mantenimiento de la aeronavegabilidad.
- K. Seguridad contra actos de interferencia ilícita.
- L. Mercancías peligrosas.
- M. Seguridad en el compartimiento de carga.
- N. Reglas para el prestador del servicio especializado, nacional e internacional.
- O. Disposiciones transitorias y final.

## RAV 111, materias que ya se leyeron

La gaceta está a dos columnas y el texto sale mezclado. Estas secciones sí
quedaron identificadas: personal gerencial y manuales (111.6, 111.7),
capacitación (111.9), certificado del explotador de servicio especializado
aeroportuario (111.11 a 111.15), normas de operación en plataforma (111.17 a
111.19), vehículos y equipos de apoyo terrestre (111.23), limpieza de cabina
(111.26), combustibles (111.27), guía de estacionamiento (111.25), pasarelas
(111.28), atención al pasajero (111.30), despacho de aeronaves (111.31) e
inspectores de operaciones de tierra (111.32, 111.33).

## Datos que el formulario ya pide

En la operación: clase regular o no regular, reglas VFR o IFR, regulación que aplica
(135, 121, 125 o 91), alternativa, copiloto, mercancías peligrosas, pasajeros y
combustible con el texto de la norma, y el tipo de servicio de la RAV 119
(pasajeros, carga, correo, combinado, a demanda, pacientes, valores).

En el reporte técnico: condición de aeronavegabilidad, en el paso de mantenimiento.

En la novedad de hangar: lugar (plataforma, hangar, maniobras o equipo) y el
vehículo o equipo, según la RAV 111.

Reglas de la operación:

- Más de 19 pasajeros avisa que esa operación se conduce según la RAV 121
  (sección 119.10).
- Mercancías peligrosas marcadas exigen autorización (capítulo L de la RAV 135).

Una base que ya existía no toma estos campos sola. El comando es
`python -m app.scripts.apply_rav_forms`.

## Dónde cae cada pantalla

- Operación aérea: RAV 119 (AOC y especificaciones) y RAV 135, capítulos B y D.
- Reporte de la tripulación, horas reales y novedad en vuelo: RAV 135, capítulos
  B y F, y la RAV 91 en la regla general de vuelo. El paso del ticket no reemplaza
  el libro de a bordo ni el control de fatiga.
- Diagnóstico, taller y aeronavegabilidad: RAV 135, capítulo J.
- Traslado y retorno: RAV 135, capítulo K, y la RAV 111 mientras la aeronave está
  en plataforma.
- Pasajeros: manifiesto de la operación (RAV 135) y atención en plataforma
  (sección 111.30). El número de personas del formulario no es el manifiesto.
- Permiso INAC: requisito de la autoridad para ese vuelo. Operar sigue exigiendo
  AOC y especificaciones vigentes (RAV 119).
- Novedad de hangar y apoyo en tierra: RAV 111.
- Gasto y conciliación: control interno de la empresa. No salen de estas gacetas.

## Lo que la norma pide y el sistema todavía no registra

- AOC, especificaciones operacionales y su vigencia.
- Cargos clave de la sección 119.39 y su aceptación.
- Límites de fatiga y tiempo de servicio (capítulo F).
- Instrucción, verificaciones y vigencia de la tripulación (capítulos E, G y H).
- Programa de mantenimiento y control de la aeronavegabilidad como expediente
  (capítulo J). El reporte técnico cubre la novedad del vuelo.
- Meteorología y límites VFR/IFR del despacho (capítulo D).
- Mercancías peligrosas y compartimiento de carga (capítulos L y M).
- Manual de operaciones y manuales de plataforma que la norma exige conservar.

Un cambio de flujo se contrasta con el capítulo de esta lista antes de tocar
el formulario o el paso.
