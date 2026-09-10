#version 440

layout(location = 0) in vec3 inPosition;
layout(location = 1) in vec4 inColor;
layout(location = 2) in float inSize;
layout(location = 3) in float inTime;

layout(location = 0) out vec4 vColor;
layout(location = 1) flat out float vPointPrimitive;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float padding0;
    vec2 viewport;
    vec4 camera;   // elevation radians, azimuth radians, zoom, point scale
    vec4 temporal; // current time, duration, lifespan, history mode
    vec4 style;    // base alpha, enabled alpha, fade curve, primitive scale
} ubuf;

out gl_PerVertex {
    vec4 gl_Position;
    float gl_PointSize;
};

void main()
{
    float ca = cos(ubuf.camera.y);
    float sa = sin(ubuf.camera.y);
    float ce = cos(ubuf.camera.x);
    float se = sin(ubuf.camera.x);

    vec3 rotated;
    rotated.x = inPosition.x * ca + inPosition.y * sa;
    rotated.y = inPosition.x * (-sa * ce)
        + inPosition.y * (ca * ce)
        + inPosition.z * se;
    rotated.z = inPosition.x * (sa * se)
        + inPosition.y * (-ca * se)
        + inPosition.z * ce;

    float perspective = 1.0 / clamp(2.75 - rotated.z * 0.38, 1.45, 4.0);
    float scale = min(ubuf.viewport.x, ubuf.viewport.y) * 0.82 * ubuf.camera.z;
    vec2 localPosition = vec2(
        ubuf.viewport.x * 0.5 + rotated.x * perspective * scale,
        ubuf.viewport.y * 0.51 - rotated.y * perspective * scale
    );
    gl_Position = ubuf.qt_Matrix * vec4(localPosition, 0.0, 1.0);

    float temporalAlpha = 1.0;
    if (ubuf.temporal.w >= -0.5) {
        if (ubuf.temporal.w < 0.5) {
            temporalAlpha = 1.0;
        } else if (ubuf.temporal.w < 1.5) {
            temporalAlpha = inTime <= ubuf.temporal.x ? 1.0 : 0.0;
        } else {
            float age = ubuf.temporal.x - inTime;
            float progress = 1.0 - age / max(ubuf.temporal.z, 0.001);
            temporalAlpha = age >= 0.0 && age <= ubuf.temporal.z
                ? pow(clamp(progress, 0.0, 1.0), max(ubuf.style.z, 0.01))
                : 0.0;
        }
    }

    float pointRadius = sqrt(max(inSize, 1.0)) * 0.32
        * max(ubuf.camera.w, 0.01) * max(ubuf.style.w, 0.01);
    gl_PointSize = clamp(pointRadius, 1.0, 28.0);
    vPointPrimitive = ubuf.style.w > 1.1 ? 1.0 : 0.0;
    vColor = vec4(
        inColor.rgb,
        inColor.a * ubuf.style.x * ubuf.style.y * temporalAlpha * ubuf.qt_Opacity
    );
}
