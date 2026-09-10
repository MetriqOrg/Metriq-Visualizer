#version 440

layout(location = 0) in vec4 color;
layout(location = 1) in vec2 localCoord;
layout(location = 2) in float primitiveKind;
layout(location = 3) in float headAmount;

layout(location = 0) out vec4 fragColor;

void main()
{
    float alpha = color.a;
    vec3 rgb = color.rgb;
    if (primitiveKind > 0.5 && primitiveKind < 1.5) {
        float radius = length(localCoord);
        if (radius > 1.0)
            discard;
        float edge = 1.0 - smoothstep(0.78, 1.0, radius);
        float core = 1.0 - smoothstep(0.0, 0.44, radius);
        float halo = (1.0 - smoothstep(0.20, 1.0, radius)) * headAmount;
        alpha *= max(edge, halo * 0.46);
        rgb = mix(rgb, vec3(0.92, 1.0, 1.0), core * headAmount * 0.72);
    }
    if (alpha <= 0.001)
        discard;
    fragColor = vec4(rgb * alpha, alpha);
}
