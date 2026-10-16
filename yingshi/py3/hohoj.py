import re,sys
from urllib.parse import quote,unquote,urljoin
from bs4 import BeautifulSoup
from base.spider import Spider
sys.path.append('..')
class Spider(Spider):
	def getName(self):return'HoHoJ'
	def init(self,tid=''):self.UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
	def homeContent(self,filter):result={};classes=[{'type_name':'有码','type_id':'search?type=censored&order=latest'},{'type_name':'无码','type_id':'search?type=uncensored'},{'type_name':'中文字幕','type_id':'search?type=chinese'},{'type_name':'欧美','type_id':'search?type=europe'},{'type_name':'巨乳美乳','type_id':'main_ctg?id=9&name=%E5%B7%A8%E4%B9%B3%E7%BE%8E%E4%B9%B3'},{'type_name':'制服诱惑','type_id':'main_ctg?id=4&name=%E5%88%B6%E6%9C%8D%E8%AA%98%E6%83%91'},{'type_name':'角色剧情','type_id':'main_ctg?id=6&name=%E8%A7%92%E8%89%B2%E5%8A%87%E6%83%85'},{'type_name':'丝袜美腿','type_id':'main_ctg?id=1&name=%E7%B5%B2%E8%A5%AA%E7%BE%8E%E8%85%BF'},{'type_name':'出轨','type_id':'main_ctg?id=7&name=%E5%87%BA%E8%BB%8C'},{'type_name':'伦理','type_id':'main_ctg?id=8&name=%E4%BA%82%E5%80%AB'},{'type_name':'凌辱','type_id':'main_ctg?id=2&name=%E5%BC%B7%E5%A7%A6%E5%87%8C%E8%BE%B1'},{'type_name':'受孕','type_id':'main_ctg?id=12&name=%E5%85%A7%E5%B0%84%E5%8F%97%E5%AD%95'},{'type_name':'多人','type_id':'main_ctg?id=5&name=%E5%A4%9AP%E7%BE%A4%E4%BA%A4'},{'type_name':'女优库','type_id':'all_models'}];result['class']=classes;return result
	def homeVideoContent(self):0
	def categoryContent(self,tid,pg,filter,extend):
		result={};videos=[];page=int(pg)
		if tid=='all_models':url=f"https://hohoj.tv/all_models"if page==1 else f"https://hohoj.tv/all_models?p={page}"
		elif tid.startswith('model?'):url=f"https://hohoj.tv/{tid}"if page==1 else f"https://hohoj.tv/{tid}&p={page}"
		else:url=f"https://hohoj.tv/{tid}"if page==1 else f"https://hohoj.tv/{tid}&p={page}"
		header={'User-Agent':self.UA};res=self.fetch(url,headers=header);soup=BeautifulSoup(res.text,'html.parser')
		if tid=='all_models':
			model_items=soup.select('div.model-list div.model')
			for item in model_items:
				a_tag=item.find('a')
				if not a_tag:continue
				href=a_tag.get('href','');img_tag=a_tag.find('img');pic=img_tag.get('src','')if img_tag else'';name_div=a_tag.find('div',class_='model-name');title=name_div.text.strip()if name_div else'';vod_id=href.lstrip('/');videos.append({'vod_id':vod_id,'vod_name':title,'vod_pic':pic,'vod_tag':'folder','vod_remarks':'女优'})
		else:
			items=soup.select('div.video-item')
			for item in items:
				a_tag=item.find('a')
				if not a_tag:continue
				href=a_tag.get('href','');video_id=href.split('id=')[-1]if'id='in href else href;img_tag=item.find('img');pic=img_tag.get('src','')if img_tag else'';title_div=item.find('div',class_='video-item-title');title=title_div.text.strip()if title_div else'';videos.append({'vod_id':video_id,'vod_name':title,'vod_pic':pic,'vod_remarks':''})
		result['list']=videos;result['page']=page;result['pagecount']=page+1;result['limit']=len(videos);result['total']=9999;return result
	def detailContent(self,array):
		vod_id=array[0]
		if vod_id.startswith('model?'):return self.categoryContent(vod_id,1,False,{})
		url=f"https://hohoj.tv/video?id={vod_id}";header={'User-Agent':self.UA};res=self.fetch(url,headers=header);soup=BeautifulSoup(res.text,'html.parser');title_el=soup.find('h1')or soup.find('title');title=title_el.text.strip()if title_el else'视频详情';img_el=soup.select_one('.video-player img, .video-info img');pic=img_el.get('src','')if img_el else'';vod={'vod_id':vod_id,'vod_name':title,'vod_pic':pic,'vod_content':title,'vod_play_from':'HoHoJ','vod_play_url':f"正片${vod_id}"};return{'list':[vod]}
	def searchContent(self,key,quick):0
	def playerContent(self,flag,id,vipFlags):
		result={};embed_url=f"https://hohoj.tv/embed?id={id}";headers={'User-Agent':self.UA,'Referer':f"https://hohoj.tv/video?id={id}"};res=self.fetch(embed_url,headers=headers);play_url='';m3u8_match=re.search('<video[^>]+src=["\\\']([^"\\\']+)["\\\']',res.text)
		if m3u8_match:play_url=m3u8_match.group(1)
		else:
			soup=BeautifulSoup(res.text,'html.parser');video_tag=soup.find('video')
			if video_tag and video_tag.get('src'):play_url=video_tag.get('src')
		result['parse']=0;result['playUrl']='';result['url']=play_url;result['header']={'User-Agent':self.UA,'Referer':embed_url};return result