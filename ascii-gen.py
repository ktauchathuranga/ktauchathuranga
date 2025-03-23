from PIL import Image, ImageEnhance
import numpy as np

ASCII_CHARS = [
    " ", ".", ":", "-", "=", "+", "*", "#", "%", "@", "M", "W", "8", "8", "B", "8", "8", "8", "8"
]

def image_to_ascii(image_path, width, height, density=10, invert=False, brightness=1.0, contrast=1.0):
    ascii_chars = ASCII_CHARS[:density]
    char_density = len(ascii_chars)
    
    img = Image.open(image_path)
    
    enhancer = ImageEnhance.Brightness(img)
    img = enhancer.enhance(brightness)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(contrast)
    
    img = img.resize((width, height))
    
    img = img.convert("L")
    
    if invert:
        img = Image.fromarray(255 - np.array(img))
    
    # Get pixel values and map to ASCII characters
    pixels = np.array(img)
    ascii_pixels = np.vectorize(lambda x: ascii_chars[x * char_density // 256])(pixels)
    
    ascii_art = "\n".join("".join(row) for row in ascii_pixels)
    return ascii_art

def ascii_to_svg(ascii_art, font_size=15, line_height=20):
    lines = ascii_art.split("\n")
    svg_lines = ["<tspan x=\"15\" y=\"30\">                                               </tspan>"]  # Add a blank line at the beginning
    svg_lines += [
        f'<tspan x="15" y="{30 + i * line_height}">{line}</tspan>'
        for i, line in enumerate(lines)
    ]
    svg_lines.append(f'<tspan x="15" y="{30 + len(lines) * line_height}">                                    </tspan>')  # Add a blank line at the end
    svg_content = '\n'.join(svg_lines)
    svg_template = f"""<text x="15" y="30" fill="#24292f" class="ascii">\n{svg_content}\n</text>"""
    return svg_template

# Main logic
image_path = "a.png"  # Replace with your image path
width = 36  # Number of characters in width
height = 23  # Number of lines

# Controls
density = 13  # Number of ASCII characters to use (1-17, where 17 is the most detailed)
invert = False  # Invert the colors (True/False)
brightness = 1  # Adjust brightness (1.0 = default)
contrast = 1  # Adjust contrast (1.0 = default)

ascii_art = image_to_ascii(image_path, width, height, density, invert, brightness, contrast)
svg_output = ascii_to_svg(ascii_art)

with open("ascii_art.svg", "w") as svg_file:
    svg_file.write(svg_output)

print("ASCII art saved to 'ascii_art.svg'")

