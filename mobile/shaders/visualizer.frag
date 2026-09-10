#version 440

layout(location = 0) in vec4 vColor;
layout(location = 1) flat in float vPointPrimitive;
layout(location = 0) out vec4 fragColor;

void main()
{
    if (vPointPrimitive > 0.5) {
        vec2 centered = gl_PointCoord - vec2(0.5);
        if (dot(centered, centered) > 0.25)
            discard;
    }
    if (vColor.a <= 0.001)
        discard;
    fragColor = vColor;
}
