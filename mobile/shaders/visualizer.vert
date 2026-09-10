#version 440

layout(location = 0) in vec3 pointA;
layout(location = 1) in vec3 pointB;
layout(location = 2) in vec4 vertexColor;
layout(location = 3) in vec2 corner;
layout(location = 4) in vec4 meta; // kind, time, size, reserved

layout(location = 0) out vec4 color;
layout(location = 1) out vec2 localCoord;
layout(location = 2) out float primitiveKind;
layout(location = 3) out float headAmount;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float progress;
    float lineWidth;
    float pointScale;
    mat4 sceneMatrix;
    vec2 viewport;
    float trailLength;
    float historyMode;
    float showLine;
    float showPoints;
    float showGrid;
    float showAxes;
    float showHead;
    float headScale;
    float haloScale;
    float baseAlpha;
    float fadeCurve;
    float pointCount;
    float padding0;
    float padding1;
} ubuf;

vec2 projectedLocal(vec3 point)
{
    vec4 clip = ubuf.sceneMatrix * vec4(point, 1.0);
    float reciprocalW = 1.0 / max(abs(clip.w), 0.00001);
    vec2 ndc = clip.xy * reciprocalW;
    return vec2((ndc.x * 0.5 + 0.5) * ubuf.viewport.x,
                (0.5 - ndc.y * 0.5) * ubuf.viewport.y);
}

float temporalAlpha(float timestamp)
{
    if (ubuf.historyMode > 1.5)
        return 1.0;
    if (ubuf.historyMode > 0.5)
        return timestamp <= ubuf.progress ? 1.0 : 0.0;
    float age = ubuf.progress - timestamp;
    if (age < 0.0 || age > ubuf.trailLength)
        return 0.0;
    float remaining = clamp(1.0 - age / max(ubuf.trailLength, 0.0001), 0.0, 1.0);
    return pow(remaining, max(ubuf.fadeCurve, 0.05));
}

void main()
{
    primitiveKind = meta.x;
    float timestamp = meta.y;
    float elementSize = max(meta.z, 0.01);
    vec2 a = projectedLocal(pointA);
    vec2 b = projectedLocal(pointB);
    vec2 localPosition = a;
    float alpha = vertexColor.a;
    headAmount = 0.0;
    localCoord = corner;

    if (primitiveKind < 0.5) {
        vec2 delta = b - a;
        float deltaLength = max(length(delta), 0.001);
        vec2 normal = vec2(-delta.y, delta.x) / deltaLength;
        localPosition = mix(a, b, corner.x)
                      + normal * corner.y * ubuf.lineWidth * elementSize * 0.5;
        alpha *= ubuf.showLine * temporalAlpha(timestamp);
        localCoord = vec2(corner.x, corner.y);
    } else if (primitiveKind < 1.5) {
        float threshold = max(1.5 / max(ubuf.pointCount, 1.0), 0.001);
        headAmount = ubuf.showHead * (1.0 - smoothstep(threshold, threshold * 2.5,
                                                       abs(timestamp - ubuf.progress)));
        float radius = 1.4 + ubuf.pointScale * elementSize * 5.2
                     + headAmount * (ubuf.headScale * 7.0 + ubuf.haloScale * 8.0);
        localPosition = a + corner * radius;
        alpha *= ubuf.showPoints * temporalAlpha(timestamp);
    } else if (primitiveKind < 2.5) {
        vec2 delta = b - a;
        float deltaLength = max(length(delta), 0.001);
        vec2 normal = vec2(-delta.y, delta.x) / deltaLength;
        localPosition = mix(a, b, corner.x) + normal * corner.y * elementSize * 0.5;
        alpha *= ubuf.showGrid;
    } else {
        vec2 delta = b - a;
        float deltaLength = max(length(delta), 0.001);
        vec2 normal = vec2(-delta.y, delta.x) / deltaLength;
        localPosition = mix(a, b, corner.x) + normal * corner.y * elementSize * 0.5;
        alpha *= ubuf.showAxes;
    }

    color = vec4(vertexColor.rgb, alpha * ubuf.baseAlpha * ubuf.qt_Opacity);
    gl_Position = ubuf.qt_Matrix * vec4(localPosition, 0.0, 1.0);
}
