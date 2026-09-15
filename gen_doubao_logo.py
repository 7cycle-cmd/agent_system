from PIL import Image, ImageDraw

img = Image.new('RGBA', (256, 256), (255, 255, 255, 0))
draw = ImageDraw.Draw(img)

# Doubao-style blue circle with white inner circle and blue dot
draw.ellipse([20, 20, 236, 236], fill=(59, 130, 246, 255), outline=(37, 99, 235, 255), width=8)
draw.ellipse([60, 60, 196, 196], fill=(255, 255, 255, 255))
draw.ellipse([100, 100, 156, 156], fill=(59, 130, 246, 255))

img.save('c:/projects/agent_system/mouse_spot_targets/doubao.png')
print('generated c:/projects/agent_system/mouse_spot_targets/doubao.png')
