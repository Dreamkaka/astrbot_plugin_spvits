from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
# 修改导入语句，使用正确的模块路径
from astrbot.api.message_components import Record, Plain
import os
import time
import requests
import re
import glob
import json

# 删除自定义的 Record 类，直接使用导入的 Record 类

@register("spvits", "Dreamkaka", "使用 VITS 模型进行文本转语音", "1.3")
class SpVitsPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 从配置文件加载参数
        self.config = context.config
        self.api_url = self.config.get("api_url", "https://artrajz-vits-simple-api.hf.space/voice/vits")
        self.llm_voice_mode = self.config.get("llm_voice_mode_default", False)
        self.max_temp_size_mb = self.config.get("max_temp_size_mb", 50)
        self.speaker = self.config.get("speaker", 281)
        self.length = self.config.get("length", 1.5)
        self.noise = self.config.get("noise", 0.33)
        self.noisew = self.config.get("noisew", 0.5)
        self.max_text_length = self.config.get("max_text_length", 100)
        self.temp_dir = os.path.join(os.path.dirname(__file__), 'temp')

    async def initialize(self):
        """插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        logger.info("VITS 语音合成插件已加载")
        logger.info(f"当前配置: API URL={self.api_url}, 说话人ID={self.speaker}")
        
        # 创建临时目录用于存储生成的语音文件
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # 初始化时清理一次临时文件
        self.cleanup_temp_files()
    
    def get_dir_size_mb(self, directory):
        """获取目录大小（MB）"""
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(directory):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.exists(fp):
                    total_size += os.path.getsize(fp)
        return total_size / (1024 * 1024)  # 转换为MB
    
    def cleanup_temp_files(self):
        """清理临时文件，保持文件夹大小在限制以内"""
        try:
            # 获取当前临时文件夹大小
            current_size_mb = self.get_dir_size_mb(self.temp_dir)
            
            # 如果小于限制，不需要清理
            if current_size_mb <= self.max_temp_size_mb:
                return
            
            logger.info(f"临时文件夹大小 {current_size_mb:.2f}MB 超过限制 {self.max_temp_size_mb}MB，开始清理...")
            
            # 获取所有临时文件并按修改时间排序
            files = glob.glob(os.path.join(self.temp_dir, "*.wav"))
            files.sort(key=os.path.getmtime)
            
            # 从最旧的文件开始删除，直到文件夹大小低于限制
            for file_path in files:
                if self.get_dir_size_mb(self.temp_dir) <= self.max_temp_size_mb:
                    break
                
                try:
                    os.remove(file_path)
                    logger.info(f"已删除临时文件: {file_path}")
                except Exception as e:
                    logger.error(f"删除临时文件失败: {file_path}, 错误: {str(e)}")
            
            # 清理后的大小
            new_size_mb = self.get_dir_size_mb(self.temp_dir)
            logger.info(f"清理完成，当前临时文件夹大小: {new_size_mb:.2f}MB")
            
        except Exception as e:
            logger.error(f"清理临时文件时出错: {str(e)}")
    
    @filter.command("say")
    async def vits_command(self, event: AstrMessageEvent):
        """使用 VITS 模型将文本转换为语音"""
        # 获取命令后的文本内容
        text = event.message_str.strip()
        
        # 移除命令前缀
        if text.startswith("/say"):
            text = text[4:].strip()
        
        if not text:
            yield event.plain_result("请输入要转换的文本，例如：/say 你好，世界！")
            return
        
        try:
            # 清理临时文件
            self.cleanup_temp_files()
            
            # 构建请求参数
            params = {
                "text": text,
                "speaker": self.speaker,  # 使用配置中的说话人
                "length": self.length,    # 使用配置中的语音长度控制
                "noise": self.noise,      # 使用配置中的噪声参数
                "noisew": self.noisew     # 使用配置中的噪声宽度参数
            }
            
            # 发送请求获取音频数据
            response = requests.get(self.api_url, params=params)
            response.raise_for_status()
            
            # 保存音频文件
            file_name = f'vits_{int(time.time())}.wav'
            file_path = os.path.join(self.temp_dir, file_name)
            
            with open(file_path, 'wb') as f:
                f.write(response.content)
            
            # 返回音频消息，使用正确的 Record 构造方法
            yield MessageEventResult([Record(file=file_path)])
            
        except Exception as e:
            error_msg = f"语音合成失败: {str(e)}"
            logger.error(error_msg)
            yield event.plain_result(error_msg)
    
    @filter.command("voice_mode")
    async def toggle_voice_mode(self, event: AstrMessageEvent):
        """切换是否将LLM回复转为语音"""
        self.llm_voice_mode = not self.llm_voice_mode
        status = "开启" if self.llm_voice_mode else "关闭"
        yield event.plain_result(f"已{status}LLM回复语音模式")
    
    # 修改这里的装饰器名称
    @filter.on_llm_response()
    async def process_llm_response(self, event: AstrMessageEvent):
        """处理LLM的回复，如果开启了语音模式则转为语音"""
        # 只处理开启了语音模式的情况
        if not self.llm_voice_mode:
            return
        
        try:
            # 获取LLM回复的文本
            text = event.message_str.strip()
            if not text:
                return
            
            # 文本太长时分段处理
            segments = self.split_text(text, self.max_text_length)
            
            for segment in segments:
                # 构建请求参数
                params = {
                    "text": segment,
                    "speaker": self.speaker,  # 使用配置中的说话人
                    "length": self.length,    # 使用配置中的语音长度控制
                    "noise": self.noise,      # 使用配置中的噪声参数
                    "noisew": self.noisew     # 使用配置中的噪声宽度参数
                }
                
                # 发送请求获取音频数据
                response = requests.get(self.api_url, params=params)
                response.raise_for_status()
                
                # 创建临时目录
                temp_dir = os.path.join(os.path.dirname(__file__), 'temp')
                os.makedirs(temp_dir, exist_ok=True)
                
                # 保存音频文件
                file_name = f'vits_llm_{int(time.time())}_{hash(segment) % 10000}.wav'
                file_path = os.path.join(temp_dir, file_name)
                
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                
                # 发送语音消息，使用正确的 Record 构造方法
                yield MessageEventResult([Record(file=file_path)])
                
        except Exception as e:
            error_msg = f"LLM回复转语音失败: {str(e)}"
            logger.error(error_msg)
            # 不向用户发送错误消息，避免打断对话流程
    
    def split_text(self, text, max_length=None):
        """将长文本分割成适合语音合成的片段"""
        # 如果未指定最大长度，使用配置中的值
        if max_length is None:
            max_length = self.max_text_length
            
        # 如果文本较短，直接返回
        if len(text) <= max_length:
            return [text]
        
        # 按句子分割
        sentences = re.split(r'([。！？.!?])', text)
        segments = []
        current_segment = ""
        
        # 合并句子，确保每个片段不超过最大长度
        for i in range(0, len(sentences), 2):
            sentence = sentences[i]
            # 添加标点符号（如果有）
            if i + 1 < len(sentences):
                sentence += sentences[i + 1]
                
            # 如果当前片段加上新句子不超过最大长度，则添加到当前片段
            if len(current_segment) + len(sentence) <= max_length:
                current_segment += sentence
            else:
                # 如果当前片段不为空，添加到结果中
                if current_segment:
                    segments.append(current_segment)
                # 开始新的片段
                current_segment = sentence
        
        # 添加最后一个片段
        if current_segment:
            segments.append(current_segment)
            
        return segments

    @filter.command("clear_temp")
    async def clear_temp_command(self, event: AstrMessageEvent):
        """手动清理临时文件"""
        try:
            before_size = self.get_dir_size_mb(self.temp_dir)
            
            # 删除所有临时文件
            files = glob.glob(os.path.join(self.temp_dir, "*.wav"))
            for file_path in files:
                try:
                    os.remove(file_path)
                except Exception as e:
                    logger.error(f"删除临时文件失败: {file_path}, 错误: {str(e)}")
            
            after_size = self.get_dir_size_mb(self.temp_dir)
            
            yield event.plain_result(f"临时文件清理完成！\n清理前: {before_size:.2f}MB\n清理后: {after_size:.2f}MB")
        
        except Exception as e:
            error_msg = f"清理临时文件失败: {str(e)}"
            logger.error(error_msg)
            yield event.plain_result(error_msg)

    async def terminate(self):
        """插件销毁方法，当插件被卸载/停用时会调用。"""
        logger.info("VITS 语音合成插件已卸载")

